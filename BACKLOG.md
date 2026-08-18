# Sprout Backlog

Purpose: track progress toward a usable, general-purpose, functional-first language.

Legend:
- Priority: `P0` (critical), `P1` (important), `P2` (later)
- Status: `[ ]` todo, `[~]` in progress, `[x]` done

## Backlog

### 1) Language Core and Safety

- [x] `P0` **FIXED 2026-08-12. BUG: CPR worker ABI mismatch — matching directly on a call to a fn with
  a type-variable result reads the payload as the tag and reads past the object.**
  **Resolution.** All four routing sites now gate on `cpr_result_known` (the Tier-2 ADT router, the
  tuple router, the do-bind router — which had no shape check at all — and `collect_worker_callees`,
  which must agree because `translate_tail_chain` chains worker-to-worker through the collected set).
  The flag rides on `fn_arities`, widened `Dict Int` → `Dict FnInfo` (`arity` + `cpr_result`) so ~200
  call sites stayed untouched; the mutual-TCO pre-pass keeps its own plain arity map. The
  `translate_tail_catchall` `Nil` arm no longer infers "no ctor list ⇒ tuple" — it hard-errors unless
  `scalar_tuple_width` confirms a tuple, so a future router/emitter disagreement is a compile error
  rather than an out-of-bounds load. Tests: the original fixture un-XFAILed, plus
  `adt_through_generic_param_shapes` (nullary + payload-equals-a-tag, both silently wrong before),
  `tests/stdlib/test_cpr_generic_result.spr` (prelude `list_fold`), and
  `tests/stdlib/compiler/test_cpr_generic_result_no_worker.spr` (IR shape, asserting BOTH that a
  tyvar-result callee emits no worker AND that a concrete one still does). Cost: ~16 ns + one box per
  affected call; zero currently-working call sites de-optimized (seed census: 279 workers, none
  mis-lowered). The gate declines **bare type variables only** — a generic declared `-> Maybe b` or
  `-> Box b` still workerizes with the constructor fully fused, so this is not "CPR off for generics".
  See the `P1` in §Sprout-IR / Model-C Codegen for why the remaining gap is narrower than it looks.
  <details><summary>Original report</summary>
  `fn ident(x: a) -> a = x` is enough — `match ident(Full(7))` dies with `runtime error: non-exhaustive
  match` while `match Full(7)` on the same value returns 7. **Root cause found 2026-08-12** (verified
  against the emitted IR): the Tier-2 CPR router decides to call `@<name>_worker` from the *call site's*
  arms alone and never checks the callee's declared result type, while `translate_tail_catchall`
  (`ast_to_ir.sprout:7724`) treats the resulting empty ctor list — empty because `type_head_name` misses
  on a type variable — as proof that the worker returns a *tuple*, and emits the all-fields convention
  `{obj[0], obj[1]}` where the call site expects `{tag, field0}`. The value itself is never corrupted;
  the two sides disagree about what the returned words mean. **Not only a crash:** when the payload
  happens to equal a valid ctor tag the match silently takes the wrong arm and returns garbage, and the
  `obj[1]` load is a genuine out-of-bounds read (`sprout_obj_alloc_arity` does not pad in normal builds).
  Trigger is the *call-site shape*, not the payload: any ctor of arity ≤ 1 including **nullary**, but not
  arity ≥ 2, not records, and not a let-bound scrutinee (the documented downstream workaround). Hits most
  generic combinators; confirmed live on the prelude's `list_fold`, which is how a downstream project
  found it. Ruled out: GC rooting (deterministic under `SPROUT_GC_STRESS=1`), the `list_fold_go` do-bind,
  and stdlib/prelude. Fix direction (needs approval): gate workerization on a concrete result type in
  both `collect_worker_callees` and the router, and restore the loud failure in the `Nil` arm.
  Full write-up + repro: `docs/bug-adt-through-generic-param-2026-08-12.md`. Quarantined fixture:
  `tests/conformance/run/adt_through_generic_param` (in that directory's `XFAIL`; the gate goes red
  with `UNEXPECTED PASS` once fixed, so the quarantine line must be removed as part of the fix).
  </details>
- [x] `P0` Add `Result e a` and core helpers in stdlib (`map`, `map_error`, `and_then`, `with_default`).
- [x] `P0` Define runtime error conventions for effectful builtins (no silent exits).
- [x] `P1` Add ergonomic helpers for control flow (`when_ok`, `when_error`, optional pipeline helpers).
- [x] `P1` Decide how builtins participate in effect tracking; host-implemented builtins now follow the same effect-typing rule as ordinary functions, with runtime/external interaction tracked via `!{IO}` and value-level `Maybe`/`Result` shapes kept separate from effectfulness.
- [x] `P0` **FIXED 2026-08-11. UNSOUND: a `do` block's `Result` short-circuit is never type-checked
  against the enclosing function's return type.**
  **Resolution.** Design: `docs/fallible-bind-typing-v0.md`. Normative rule: `docs/spec-v0.md` §5.9,
  written from scratch (the plain fallible `<-` bind had no section at all, and §5.8's `(§11)`
  cross-reference was dangling). Landed in three parts:
  1. *Linearity predicate* (`linear_check.sprout`) — re-keyed from the block's tail type to the
     bind's own RHS type, so `!{IO}` no longer exempts the rule. Verified against the pre-fix
     compiler in **both** directions: it accepted the reverted `bench/http_worker_pool` leak with 0
     errors, and it wrongly *rejected* a non-fallible bind followed by a consume in a `Result` block.
     `sc_block` came out of eleven signatures — one fallible bind condemns everything after it, so
     the outermost one already sees the whole remainder and no flag needs threading.
  2. *`mutmatrix_at`* (`stdlib/mutable.sprout`) — the migration target, mirroring `mutvec_at`. Bounds
     are checked on `(r, c)` and not left to the flat check, because an out-of-range COLUMN lands
     inside the backing vector (measured: reading `(0,3)` of a 2×3 returned cell `(1,0)`).
  3. *The typing rule* (`infer.sprout`) — one unification of the block's type against a constructed
     `Result E ?a` / `Maybe ?a` in `infer_do_steps`' `Nil` arm, plus a `DoFamily` accumulator
     carrying the bind's type and position. Constructed rather than head-compared, which is what
     closes the unresolved-tyvar hole: a tyvar tail (`panic : a`) was the ONLY way to reach the
     wrong-family case, and it compiled — the `Nothing` box matched neither `Ok` nor `Err`.
  **Migration: 110 sites, no deprecations.** 68 `mutvec_get` → `mutvec_at`, 19 `mutmatrix_get` →
  `mutmatrix_at`, 14 `_ <- write_file(…)` → bare statement, and 9 hand-migrated `Result` sites
  (`probe_ir -> String`, four `tcp_accept` fixtures). Codegen bonus: `mutvec_get_worker` is now DCE'd
  out of `astar` and `neural_network_train_xor` — the unboxed worker existed only for the `<-`
  short-circuit, so the migration removes a `Maybe` allocation per read in those hot loops.
  **Diagnostics: complete.** Three of the nine negative fixtures initially landed on `check_fn_body`'s
  generic `Return type mismatch` rather than the tailored diagnostic, because their conflict surfaces
  against the *signature* after the block-level unification succeeds. Closed the same day by the `P2`
  below (`return_mismatch_body_err`); all nine now name the bind, and `bench/` gained a gate so the
  directory the leak lived in cannot rot again.
  <details><summary>Original report (kept for the measurements)</summary>

  Found 2026-08-11 while measuring the blast radius of a proposed
  "discarded fallible bind" lint (`docs/fallible-bind-diagnostic-v0.md`) — the lint was aimed at a
  style problem and the underlying defect turned out to be **type confusion**, which is why it is
  filed here at `P0` rather than as a diagnostics nicety.
  **Mechanism, read off the emitted IR.** For `x <- e` where `e : Result E A`, codegen emits a
  short-circuit that allocates a *fresh `Err` box* and returns it from the enclosing function, and the
  `phi` merging it with the success value is typed `i64` — so nothing ever checks that the enclosing
  function can carry an error, or carry *that* error:
  ```llvm
  do_short_2:                                        ; Err path, in `fn returns_int() -> Int !{IO}`
    %t$5 = call i64 @sprout_alloc_obj(i64 8, i64 1)  ; a new Err box
    store i64 %t$1, ptr %t$5$f0
  do_cont_2:
    %t$6 = add i64 0, 999
  do_done_2:
    %t$7 = phi i64 [%t$5, %do_short_2], [%t$6, %do_cont_2]
    ret i64 %t$7                                     ; an Err BOX or an Int, from `-> Int`
  ```
  **Two observable consequences, both measured, both compile clean today:**
  1. *Enclosing return type is not a `Result`.* The `Err` box is returned as that type.
     `fn returns_int() -> Int !{IO}` binding a failing `Result String Int` returned
     **35184372088840** (`0x200000000008` — a heap pointer printed as an `Int`). The same shape at
     `-> String !{IO}` returned a pointer *interpreted as a CSTR*, printing `H\xef\xbf\xbdn` — i.e.
     reading arbitrary heap bytes as text, exactly the class `SPROUT_GC_HDRCHECK` exists to catch.
  2. *Enclosing return type is a `Result` with a DIFFERENT error type.* The error types are not
     unified: `fn mismatched() -> Result Int Int` binding a `Result String Int` compiles, and the
     `"boom"` String arrives as `Err e` with `e : Int` (printed **4377984392**, the string pointer).
  **Boundaries, so the fix is not mis-scoped.** Not IO-specific — a wholly pure
  `fn pure_bind() -> Int` binding a pure `Result String Int` is equally accepted and equally wrong.
  Not affected by the binder: `_ <- e` emits the identical short-circuit, so `_ <-` is **not** a
  soundness opt-out, only a silenced name. `Unit`-returning functions are the sole *unobservable*
  case, because the returned `Err` box is discarded by convention — the type lie is the same, it
  simply has no witness.
  **SCOPE — `Maybe` binds have the identical defect, and are where nearly all of the blast radius
  is** (measured 2026-08-11, after the `Result` facets above). `infer.do_unwrap_type`
  (`infer.sprout:3627`) peels `Maybe a -> a` for the binder exactly as it peels `Result e a -> a`, and
  the same nothing relates the short-circuit to the enclosing return type. `fn returns_int() -> Int`
  binding a `Maybe Int` emits `%t = call i64 @sprout_alloc_obj(i64 0, i64 0)` (a `Nothing` box) in
  `do_short_*` and returns it — measured **35184372088840**, the same pointer-as-`Int`.
  **It also defeats the EXHAUSTIVENESS CHECKER, which is worse than the `Result` facet.** A `Maybe`
  short-circuit returned from `-> List Int` produces a value matching *neither* `Nil` *nor* `[h | t]`,
  so a match the checker proved total (spec §5.5, the `P1` W5/F-EXH work below) dies at runtime with
  `runtime error: non-exhaustive match`. A landed soundness feature is therefore only as sound as the
  absence of a fallible bind in a non-matching function — the same generalisation as the linearity
  facet below, now for a second checker.
  **Measured blast radius: 87 `Maybe` sites** (vs 21 + 1 for `Result`), of which **57 are `Unit`**
  (unobservable today) and **30 are observable**: 15 `-> Int`, 12 `-> Double`, 1 `-> String`, and
  **2 `-> List (Int, Int, Int, Int)` in the shipped `examples/astar.sprout`** — the
  exhaustiveness-defeating shape, latent only because the indices happen to be in range. Callee
  distribution is dominated by `mutvec_get` / `mutmatrix_get`.
  **The `Maybe` migration is a rename, not a one-token deletion.** These sites *want* the value —
  e.g. `examples/neural_network_train_xor.sprout:50`, `w1_0 <- mutvec_get(w, idx_w1(j, 0))` in
  `fn hidden_out(...) -> Double`, where the author knows the index is in range, so dropping the bind
  loses `w1_0`. But all 87 are just **two callees** — `mutvec_get` (68) and `mutmatrix_get` (19) — and
  the replacement mostly exists already: **`mutvec_at`** (`mutable.sprout:37`, exported) routes through
  `vector_get_direct`, which IS bounds-checked (`sprout_runtime.c:7312`, `tcp_fail("vector_get_direct:
  index out of bounds")`) — "unchecked read" in its comment means "no `Maybe` box", not "no bounds
  check". So 68 sites are a one-identifier rename that also *removes* a `Maybe` allocation per read in
  hot numeric loops, and the only new API needed is `mutmatrix_at` (~4 lines mirroring `mutvec_at`;
  today only `mutmatrix_at_or` exists). Callers wanting a default already have `vec_get_or`,
  `mutmatrix_at_or`, `maybe_with_default`, `result_with_default`.
  **The spec's linear rule is a WRONG NORMATIVE SENTENCE, which is what switched the checker off.**
  §5.8 already says "A consume may not follow a fallible bind", but keys it on "a block whose type is
  `Maybe` or `Result`" and adds "Effectful blocks (`!{IO}`) run every step and are unaffected". Both
  are wrong: the condition should be *the presence of a fallible bind*, and `!{IO}` is orthogonal to
  short-circuiting. `linear_check.sprout:465` implements the sentence literally
  (`lin_do(steps, block_short_circuits(dty), …)` on the block's tail type), so `handle`'s `Unit` block
  type made `sc_block = false` and `conditional_consume` bailed at `:922` before looking at anything.
  The diagnostic itself (`:269-270`) is already correctly worded — only its trigger is wrong. Also
  `(§11)` at `spec-v0.md:870` is a dangling cross-reference: `§11` occurs once and no such section
  exists, because the plain fallible `<-` bind has **no normative section at all** (§5.2.2 covers only
  the refutable `else` form).
  **Design: `docs/fallible-bind-typing-v0.md`** — full proposal with a survey verified by running
  rustc 1.75.0 / GHC 9.10.1 / swiftc 6.2.4 / ocamlc 5.1.0 (4 of 4 reject; none warns), the root cause
  (`infer_do_steps` types a block as its LAST step at `infer.sprout:3584-3587`, and `check_fn_body:5518`
  unifies exactly that — so the tail IS checked and the short-circuit is not), and a fix that needs one
  unification rather than new plumbing (`short_circuit_family` and the per-block `family` accumulator
  already exist and already reject *mixed* families). Awaiting approval.
  **3. THE WORST CONSEQUENCE, and the reason this is `P0` rather than `P1`: the short-circuit's
  control-flow edges are invisible to the LINEARITY CHECKER, so a `consuming` value's exactly-once
  obligation can be silently skipped.** The type confusion above produces a wrong *value*; this breaks
  a *guarantee* the language advertises. Found in `bench/http_worker_pool/{pool,spawn}_server.sprout`:
  ```sprout
  fn handle(conn: consuming TcpConnection) -> Unit !{IO} =
    do
      _ <- read_avail_timeout(conn, 5000)   # Err -> returns early
      _ <- write_all_utf8(conn, response)   # Err -> returns early
      close(conn)                            # the ONLY consume, on ONE of three paths
  ```
  The emitted IR called `stdlib.net.close` only from `do_cont_10`; both `do_short_*` blocks allocated
  an `Err` box and branched straight to the return. So a read timeout — routine under benchmark load —
  leaked the descriptor, and **the checker raised nothing**. The comment directly above that code
  asserted the obligation was "discharged exactly here on every path"; it was discharged on one of
  three. The same shape was present in `tests/task_io_smoke/http_conn_error_survives.spr`
  (`crash_after_send`, `good_send_and_report`), where the skip would additionally have bypassed
  `chan_send(ready, 1)` and hung the peer. All six sites are fixed (2026-08-11) by using a BARE
  STATEMENT instead of `_ <-`; `handle` is now branch-free, `entry -> close -> ret`.
  Generalisation to take seriously: **every `consuming` proof in the codebase is only as strong as the
  absence of a fallible bind before the consume**, because the checker is reasoning about a
  control-flow graph that is not the one being compiled. The fix must therefore cover the checker, not
  only the typing rule.
  **Fix direction.** One missing unification explains both type-level consequences: the short-circuit's type must
  be required to match the enclosing function's declared return type — i.e. `x <- (e : Result E A)` is
  well-typed only in a function returning `Result E' A'` with `E ~ E'`, and dually `x <- (e : Maybe A)`
  only in a function returning `Maybe A'`. That is a type ERROR, not a warning.
  **The `Result` migration needs no new syntax and no new API, because the discard form already exists
  and is already sound** (the `Maybe` migration does — see SCOPE above). A BARE `Result`-valued statement in a `do` block runs the call, discards the whole
  `Result`, and CONTINUES — verified: `fn bare_stmt() -> Int !{IO}` with a bare failing call returns a
  well-typed `999`, and a `Unit` version continues past the failure. So the three forms are:
  | Form | Meaning |
  | --- | --- |
  | `x <- e` | want the value; the failure propagates (must type-check against the enclosing return type) |
  | `_ <- e` | do not want the value; the failure still propagates |
  | `e` (bare statement) | run it, discard the `Result`, continue |
  The trap today is that `_ <- e` *reads* as the third row and *behaves* as the second. Measured
  distribution: `_ <-` appears in a `Result`-returning function exactly **once** in the whole tree
  (`parser.sprout:1346`, `_ <- validate_ctor_where(...)`, whose only purpose is to fail — legitimate,
  and unaffected by this rule), versus 21 times in non-`Result` functions where the author meant the
  bare-statement semantics. So `_ <-` should KEEP its meaning; the 21 sites are one-token deletions.
  Do NOT "fix" this by redefining `_ <-` to stop propagating: whether an error propagates should not
  depend on whether the success value was named, and it would silently break that parser site. Haskell gets this free because a `do` block's monad is fixed by its type; Sprout's `do` is
  overloaded across `!{IO}` sequencing and `Result` short-circuiting, so the rule has to be stated
  explicitly. Note the consequence for the lint proposal: its prior-art survey (Rust
  `unused_must_use`, Swift SE-0047, GHC `-Wunused-do-bind`) is about *discarding a value*, a style
  concern where "warn, never error" is the right answer — importing that consensus here would leave
  memory-unsafe code compiling with a warning. Different problem, different severity.
  **Known affected sites** (latent, not live — see the measurement in the doc): `probe_ir -> String`
  in `tests/stdlib/test_unresolved_dict_poison.spr` is the dangerous shape; `accept_forever -> Int` in
  three `tests/task_io_smoke/` fixtures is the `Int` shape, and cannot currently fire because
  `tcp_accept` parks rather than returning `Err` there. The one `Unit` site in production
  (`run_check_iface`) is fixed. Needs: spec wording for the typing rule, positive/negative checker
  tests, and a decision on whether the `Unit` case is permitted explicitly or also rejected.
  </details>
- [x] `P2` **Point the fallible-bind diagnostic at the bind when the conflict is with the SIGNATURE.
  FIXED 2026-08-11**, same day as the `P0` it followed. `do_block_carries` constrains the block against
  `Result E ?a` / `Maybe ?a`, which is enough for soundness but leaves two shapes to be caught later, by
  `check_fn_body`'s unification against the declared return type: a same-family different-error bind
  (the block's error slot was free, so the bind filled it in) and the wrong-family-via-tyvar-tail case
  (the tyvar absorbs the block constraint, so only the signature disagrees). Both reported
  `Return type mismatch in <fn>: Type mismatch: String vs Int` — naming the function, and neither the
  bind nor the fix.
  **Fix:** when that unification fails and the body is a `do` block containing a fallible bind,
  `return_mismatch_body_err` re-blames the bind, reusing `fallible_bind_msg` with the *declared return
  type* as its target. No threading was needed after all — `check_fn_body` already has the typed body
  and the return type in scope, so it can look for the cause rather than be told about it.
  `fallible_bind_msg` gained a `target_desc` parameter so the same three messages read correctly whether
  the thing that cannot carry the failure is "the block" or "`<fn>`". All three now point at the `<-`
  line. The generic wording is preserved for every other cause of a return-type mismatch (verified with
  a `do`-body and a non-`do`-body probe), so this is strictly additive.
- [x] `P2` **`merge_effects` drops one variable side** — DONE 2026-08-16 alongside the effect-report work below. Two distinct effect variables now merge into an `EffectRow` instead of one silently winning. It was unreachable while every callee reported pure; wiring the callee effect through `infer_call_var` made it live, so it was fixed rather than left as a known-wrong input to the census. Original finding: `docs/fundamentals-code-review-handoff-2026-07-03.md`.
- [x] `P2` **Enforce the effect rules (spec-v0.md §7 rules 8, 9, 11) — DONE 2026-08-16.** `fn shout(s: String) -> Unit = print(s)` is now a compile error; spec §7's enforcement note is flipped and README's "Effects in types" bullet no longer overclaims. Cost: **zero source annotations, zero correct programs rejected** — the four preceding changes below took the corpus to zero real gaps first, which is what the instrument existed to establish. Enforcement is a POST-PASS in `checker.typecheck_typed` over the reports `infer` already collected, not an error at the declaration boundary, so (a) every gap is named in one compile rather than one-per-compile, and (b) `--phase effects` calls `typecheck_typed_with_effects` directly and keeps enumerating — the census instrument survives enforcement, which matters most when somebody is migrating against it. Both share `unifier.effect_report_is_gap`, now a single definition. Fixtures: `type_error/effect_pure_body_does_io` (fn boundary), `type_error/effect_pure_instance_method_does_io` (instance boundary — separate on purpose; the census's original bug was instrumenting only the `fn` site), `run/effect_over_declared_ok` (subsumption still accepted — a checker that unified rather than subsumed would reject legal code and the type_error fixtures would not notice). `docs/effect-enforcement-v0.md` §11. Rules 9 and 11 followed the same day — this entry originally claimed they were unenforced, which was wrong; see the CORRECTION below. History of how it got there:
  - **The producer side is fixed and `--phase effects` reports declared-vs-inferred** (2026-08-16). Six sites were broken, not three; the two the earlier note missed are `type_from_ast` discarding an arrow's effect (the *parser had already recorded it*) and `infer_lambda` building pure lambda types. The decisive one was `infer_call_var` passing a literal `EffectPure` as the callee effect of every named call.
  - **CORRECTION to what this entry said on 2026-08-15:** it claimed fixing `build_fn_type_modes` needed a language decision — *"for a curried `fn f(a, b) -> C !{IO}`, which arrow carries the effect? … partial application makes the choice observable."* Both halves are false. Sprout is n-ary and an application is never a partial application (spec §5.3); `_` placeholders desugar to lambdas at parse time. And `parser.make_type_arrow` had already made the choice — innermost arrow — since long before. No decision was needed.
  - **Corpus census, 4337 unique declarations (all 124 files, incl. 83 instance methods): 12 gaps.** Not the large number this entry predicted. 653 bodies infer `!{IO}`, 13 over-declare (allowed — subsumption, not unification), 22 carry effect vars. **None of the 12 is a function that quietly does IO:** 9 are `panic` in an unreachable internal-error arm, 3 are the documented lambda-construction over-approximation.
  - **Is `panic` an effect? — SETTLED 2026-08-16: no. `panic : String -> a`, pure.** Six languages verified against primary sources (Koka, Haskell, Rust, Java, Swift, Zig): none folds abort into its I/O channel; Koka alone tracks it, as `exn`, a separate weaker effect *inside* its `pure` alias. Java/Swift/Zig each track recoverable failure in a signature and deliberately exempt the abort — despite every one of those aborts also printing a diagnostic, which was the one real counter-argument (`panic` → `tcp_fail` → `fprintf(stderr, …)`). It loses because an effect matters when a *continuation* can observe it, and an abort has none — **and because Sprout already agreed**: verifying the draft's "panic is the only aborting builtin" claim found ~187 `tcp_fail` sites behind builtins declared *pure* (`vector_length` aborts on a null vector), so aborting-is-not-an-effect was the de facto rule and `panic` was the lone inconsistency, not a case needing an exception. Spec §6 amended (the builtin rule now reads "…in a way the rest of the program can observe", with the abort case normative); survey in `docs/effect-enforcement-v0.md` §6; justification in `runtime/APPROVED_BUILTINS`; pinned by `tests/effects/canaries.spr` `unreachable_arm`. **Re-measured after the change: 12 gaps → 3**, all three the known lambda over-approximation, so rule 8 now costs zero source annotations.
  - **Effect-variable solving — DONE 2026-08-16.** `!{e}` is now quantified, freshened per instantiation, and bound by unification, so `list_each(print, xs)` is caught. Two things were missing and each made the other useless: every declaration site passed `Nil` for `Scheme`'s effect-var field (so *every* `!{e}` in the program was one shared variable — `build_effect_repl` only freshens what a scheme names), and `unify_applied` matched `TFunc p r _ o`, discarding the field. `unify_types` now returns a `Unified` carrying both substitutions. **Measured on an identical 5859-declaration corpus: unresolved effect variables 38 → 8**, the 8 being the effect-polymorphic HOFs themselves, where unbound is correct. Design notes and the three decisions worth keeping (arrow-position unification is total; `build_fn_type_like` inherits the effect; why both substitutions travel as one value) in `docs/effect-enforcement-v0.md` §9.
  - **Closure construction is pure — DONE 2026-08-16.** `infer_lambda_expected` returned the lambda *body's* effect as the *expression's*, so every function that built an effectful closure read as effectful. That was all **six** real corpus gaps (`log.stderr_logger`, `http_middleware.with_logging`, `http_web_server.routes`, `fixture_b1_partial.get_at`, `capture_logger` ×2) and it is why enforcing would have rejected six correct programs. Removing it also required `infer_call_general` to read the callee's arrow effect, which it never did — two effects meet at a call (evaluating the callee expression, and invoking the result) and only the first was counted, so a directly-applied `(\x -> print(x))(s)` was caught solely by the over-approximation. **Corpus real gaps: 6 → 0.** Canaries `make_shouter` / `apply_now` / `via_local` pin all three ways an effect reaches a call site. `docs/effect-enforcement-v0.md` §10.
  - **CORRECTION (2026-08-16, same day): rules 9 and 11 ARE enforced; the line above claiming otherwise was wrong.** Rule 11 was already covered — it is rule 8 stated operationally, since a body calling an `!{IO}` function infers `!{IO}` and a pure signature does not admit it (verified: `fn pure_calls_io(s: String) -> Unit = writer(s)` is rejected). Rule 9 had a real, reachable hole and is now closed: two DISTINCT effect variables meeting in one body inferred a multi-label row (`!{$e1, $e0}`), which §7 does not define and no v0 use site can instantiate, and nothing rejected it. Reachable only because effect variables became per-instantiation fresh — before that every `!{e}` was one shared variable. Not a soundness hole (`IO` wins every merge, so a row never contains it) but the same silent inference-vs-spec disagreement that hid the original effect bug. **Decision (Kuba): reject, do not widen §7 to support rows** — rule 9 already says *singleton*, so two variables were never conformant. Migration cost zero: exactly one declaration in 5927 infers a row, and it is the fixture written to demonstrate the bug. `--phase effects` gained a third verdict (`ROW`) so the report still predicts exactly what the checker rejects. Fixtures `type_error/effect_two_variables` and `run/effect_one_variable_ok` (the over-correction guard — a check rejecting any variable-valued effect would kill every prelude HOF). `docs/effect-enforcement-v0.md` §12.
  - **CORRECTION to the entry above (2026-08-16, from a code review of it): rule 9 was checked on the WRONG SIDE and was wrong in both directions. Fixed.** It tested whether the *inferred* effect was a multi-label row, which is a proxy for the property rule 9 names — how many effect variables the *signature* quantifies — and §9's per-instantiation freshening broke the proxy both ways. **False rejection:** two fresh instantiations of ONE variable also produce a row, so `fn maybe_do(n: Int) -> Unit !{e}` called twice in a body was rejected for "combining two effect variables" when it named one, with a suggested fix already true of the source. Unfixable downstream — both row members are unbound, so no substitution relates them. **False acceptance:** a signature naming two variables whose body never combines them builds no row, so `fn unused_second(f: Int -> Unit !{e}, g: Int -> Unit !{d}, n: Int) -> Unit !{e} = f(n)` compiled clean, contradicting the same commit's spec edit. The check now reads the declared `Scheme` (`types.scheme_effect_vars`, scanning every arrow, plus `types.scheme_row_labels` for a written row), so the diagnostic can name the real source variables instead of a bare count. **The "migration cost zero / zero correct programs rejected" claim above was not falsified but was not evidence either: 0 of 2951 in-tree declarations hit the false rejection**, because it needs an *unpinned* effect variable (one with no parameter to constrain it) and every prelude HOF is pinned — which is also why §12's two fixtures, both pinned, could not have caught it. Dropping the inferred side opens no rule-8 hole: `merge_effects` matches `EffectIO` in both argument positions before building any row, verified on the sharpest shape in both orders. Also fixed here: the summary double-counted rows as unresolved effect vars (the line a migration estimate is read off), and `!{IO, e}` was reported as "two effect variables", counting the concrete `IO` as one — it is now rejected under its own wording as an undefined row form, and the scan reaches parameter arrows. New fixtures `run/effect_one_variable_unpinned_ok`, `type_error/effect_two_variables_uncombined`, `type_error/effect_mixed_row`, `type_error/effect_var_merged_with_io`, `type_error/effect_two_variables_instance` (rule 9 at the instance boundary, whose declared scheme is built separately — the twin of rule 8's instance fixture). **Known limitation left open:** a class METHOD SIGNATURE is not checked for rule 9 — it has no body, so no effect report is recorded. Any instance of the class is rejected, so only a class declared and never instantiated escapes. `docs/effect-enforcement-v0.md` §13.
  - **Still open:** writing an inferred effect back to the env, so the report is still ONE WAVE rather than a fixed point — a caller of a mis-declared function is not flagged until that function is annotated and the compile is repeated. In an enforced world that is less alarming than it was as a report (the callee now cannot be mis-declared in the first place), but it still means `--phase effects` counts are a lower bound where a gap is introduced and fixed in the same pass.
  - **Minor, still open (two twins of the same shape):** `infer.call_effect_of` with `argc <= 0` returns `scheme_effects(scheme)` raw — an unfreshened program-level `EffectVar`; and `infer_call_general`'s `arrows_effect(t, argc)` returns `Pure` for `argc <= 0` with no scheme fallback, so a zero-arg call on an *expression* callee (a thunk from a record field or an `if`) loses its effect. Both are conservative (accept, do not reject) and the census shows nothing in-tree hits either.
  - ~~**Also still open:** … the conformance fixture for `fn shout(s: String) -> Unit = print(s)`, which cannot land before the implementation because `tests/conformance/type_error/` has no `XFAIL` manifest.~~ **STALE, struck 2026-08-16** — the fixture landed with the implementation as `tests/conformance/type_error/effect_pure_body_does_io`, so the ordering problem never bit. The env-write-back half of this bullet is the same item as two bullets up; only that one is still open.
  - **Aligned in passing (2026-08-16) — LATENT, not an observed defect.** `iface_codec` carries a second copy of the AST-to-type walk and it had diverged three ways from `infer`'s: its own `effect_from_maybe_labels` built `EffectRow(["IO"])` where infer's built `EffectIO` (two spellings of one value, and `unify_effects` has no rule relating them); `typeexpr_to_type_with_vars` discarded a `TypeArrow`'s effect labels; and `params_to_func_type` built every arrow pure, so a method's declared effect reached the scheme's effect field and nothing in its type. **No program was ever misjudged:** nothing type-checks against a decoded iface — `decode_iface_file`'s only consumer is `--check-iface`, which counts entries for well-formedness — so the wrong arrows were written to disk and read by nobody. Aligned because the day a consumer appears is the worst day to find the two walks disagree. Verified by diffing the emitted iface before and after (`emit`'s inner arrow `(EffectPure)` → `(EffectIO)`; `run_with`'s quantified effect vars `()` → `(e)`), and pinned by three assertions in `just effect-report-smoke` against `tests/effects/iface_effects.sprout` — a module-header fixture, because `canaries.spr` has none and so cannot reach this path at all.
  - Note the spec's §7 enforcement note says effects are "carried on `TFunc`". That was inaccurate when written — they were carried on `Scheme` and the arrows were pure — and is now true.
- [ ] `P2` **Top-level `let` initializers are not checked for purity — the last open W6 sub-item.** Spec §6 states it normatively ("Because top-level `let` bindings must be pure, imported modules do not perform effectful initialization merely by being loaded") and nothing checks it: `let boom = print("at load time")` type-checks clean, binding `main.boom : Unit` (verified 2026-08-16 against `--phase check`). Same root cause as the rule-8 hole one entry above — `LetDecl` (`infer.sprout:2955-2967`) discards the initializer's inferred effect (`InferOk typed_expr s1 _ _`) — but the enforcement pass that closed the `fn` and instance-method boundaries did not extend to it. **This is a design item, not a patch**, for two reasons. (1) It meets the **value restriction** at the same line: `LetDecl` also calls `unifier.generalize` on *any* expression, and both fixes turn on the same question of what a top-level initializer is allowed to be (`docs/fundamentals-code-review-handoff-2026-07-03.md` §W3, F-VALRESTR). Deciding them separately risks two passes disagreeing about one construct. (2) There is nothing to compare an inferred effect *against*. Unlike rule 8 this is not subsumption against a signature — spec §5.2 says flatly "At top level, `let` initializers must be pure", so it is a prohibition the programmer cannot opt out of, and the diagnostic has to say so rather than suggest an annotation. Note a `let` annotation *does* accept effect syntax — `let boom: Unit !{IO} = print("x")` parses and is accepted (verified 2026-08-16) — but the effect is discarded, the binding still types as `Unit`, and no annotation makes an effectful initializer legal. Whether that syntax should be a parse error is part of the same call. **Do not read the annotation as a declared-effect position** until the design says it is one. **Blast radius unmeasured, and the instrument does not reach it yet:** `--phase effects` does *not* enumerate top-level `let`s — verified 2026-08-16, the probe above adds `main.main` to the census and never `main.boom`. So the first step is extending the census to `LetDecl`, not writing the check; that is the same order rule 8 followed (measure with no check in the compiler, then enforce), and it is cheap because the report needs the same effect `LetDecl` currently discards. For scale: the tree has **215** top-level `let` declarations across `stdlib/`, `examples/`, `tests/` and `bench/` (`^(export )?let `); how many are effectful is exactly what the extended census would answer. Origin: `docs/fundamentals-code-review-handoff-2026-07-03.md` §W6 "Same campaign"; spec §6 carries a not-yet-enforced note pointing here.
- [ ] `P2` **An unknown effect label is accepted and inert — `!{NOPE}` type-checks.** Found 2026-08-16 while closing out W6. Spec §7 rule 9 admits exactly three annotation forms: a single concrete effect "supported in v0" (`IO` is the only one), a single effect variable, or omitted. An unrecognised uppercase label is none of these and is silently accepted: `fn nope() -> Unit !{NOPE} = ()` type-checks, `--phase check` prints `main.nope : Unit !{NOPE}`, and a *pure* caller of it — `fn pure_caller() -> Unit = nope()` — is accepted too, so the label is invisible to the rule-8/11 post-pass. Same on a top-level `let` annotation (`let boom: Unit !{NOPE} = print("x")`, accepted). **Why it matters now rather than before:** until 2026-08-16 no effect annotation was checked, so an inert one cost nothing; enforcement makes `!{IO}` a contract the compiler verifies, and this is the one spelling that opts out of it silently. Someone writing `!{Net}` or `!{Async}` — plausible guesses at a v0 surface that does not exist — gets a signature that reads as a promise and is enforced as nothing. Contrast the case §12 decided the hard way: a *second effect variable* is rejected rather than widened, and the reasoning ("§7 already says what is admissible; do not widen it") applies here unchanged. Fix is small and local — validate the label set where `effect_from_maybe_labels` builds an `Effect`, and reject anything that is neither `IO` nor a lowercase variable, with the same diagnostic shape as rule 9's. **Migration cost is zero, measured:** every effect annotation in `stdlib/`, `examples/`, `tests/` and `bench/` was enumerated (`!\{[^}]*\}`, 2026-08-16) and the only labels written are `IO` (2020) and lowercase variables `e`/`d`/`q`/`f` (69); the multi-label spellings that appear (`!{IO, e}` ×6, `!{e, d}`, `!{a, b}`) are the rule-9 negative fixtures, and the remaining hits are the compiler's own string-building code. So the check can land as an error with no source changes. **One form the survey did turn up and the spec does not define: `!{}`**, an empty row, written in `tests/stdlib/test_eta_forwarding.spr` (×2) and `test_devirt_classmethods.spr` and evidently meaning "pure". Decide it with the same change — either §7 rule 9 gains it as a spelling of purity, or those three signatures drop it — because a validator written from rule 9 as it stands would reject all three.
- [ ] `P2` Move `stdlib.compiler` to a dedicated tooling/compiler namespace once the non-stdlib tooling-package model is settled.
- [ ] `P2` Add syntactic sugar for `Ref` operations in do-notation: `:=` for `ref_write`, `<~` for a ref-read bind step, and `var x = expr` as `x <- ref_new(expr)`.
- [x] `P2` Add effect-sequencing sugar for `IO Unit` flows (`do` blocks or a dedicated sequencing operator).
- [x] `P2` Align function composition operator direction with Elm/F# conventions; `f >> g` now means `g(f(x))` and `f << g` means `f(g(x))`.
- [x] `P1` Add string interpolation syntax: backtick template literals `` `hello ${name}!` `` desugar to `string_concat_many` calls; `StringTemplateExpr`/`TemplateExprPart` AST nodes added; lexer emits 5 new token kinds (`TEMPLATE_START`, `TEMPLATE_LIT`, `TEMPLATE_INTERP_START`, `TEMPLATE_INTERP_END`, `TEMPLATE_END`); desugar runs in Python typechecker before inference, and mirrored in the Sprout bootstrap checker (`checker.sprout`); parser + checker parity tests pass with `string_template_basic.spr` in corpus.
- [x] `P1` Add compile-time pattern match exhaustiveness checking: `infer_branches` (Nil case) in `infer.sprout` now calls `pure_exhaustiveness_check` via `unifier.get_ctor_map`; `InferState` gained a third `Ref (Dict (List String))` field (`ctor_map`) populated by `build_ctor_map` in `typecheck_decls`; non-exhaustive matches on known ADTs produce a `[DIAG]` error naming the missing constructors; wildcard/variable patterns suppress the check; 4 new test cases in `test_checker.spr` cover the non-exhaustive, exhaustive, wildcard, and variable-pattern cases; bootstrap seed updated to fixed point. ADT name in error is fully-qualified (e.g. `stdlib.Color`) — stripped display is tracked as a follow-up.
- [x] `P1` W5 (F-EXH): deepen exhaustiveness + add unreachable-branch diagnostics (fundamentals review). Replaced the top-level ctor-name-set check with **per-column value-space coverage** in `infer.sprout` (`coverage_gap`/`adt_gap`/`tuple_gap`/`columns_covered`): a constructor is covered only when each field column covers the field's type (field types read via the ctor scheme, mirroring `infer_ctor_pattern_typed`); `Bool` needs both literals, `Unit` the unit pattern, `Int`/`String`/`Char` a catch-all. This rejects nested gaps like `Just 1 | Nothing` and `match b:Bool with | true -> …` that the old name-set check accepted. Added a **sound top-level unreachability check** (`unreachable_check`, spec §5.5/§10.12): catch-all shadow, duplicate literal, constructor already fully covered, or all constructors covered — using purely structural signals (never per-column coverage, which over-accepts products and would falsely flag reachable branches like `unify_applied`'s trailing `_`). Warn-only blast-radius survey first (advisor discipline): self-compile + examples + tests clean → one-shot flip, no staged rollout. Fixtures: `tests/conformance/type_error/{nested_ctor_nonexhaustive,bool_literal_nonexhaustive,unreachable_after_catchall,unreachable_after_full_adt}` + positive `tests/conformance/run/{exhaustive_match_shapes,tuple_catchall_reachable}`. Updated 3 codegen tests whose embedded programs were genuinely non-exhaustive/unreachable (T-match-6/8 → rejection guards, N4 → +catch-all). Spec §5.5 updated. **Completion (own-module ADTs):** `build_ctor_map` now keys by the bare type name (`string.after_last_dot`), matching the bare names `lookup_type_var` already produces for scrutinee types — so ADTs declared in the same module (keyed `main.C`, looked up as `C`) are exhaustiveness-checked via a plain `dict_get`, no per-lookup key scan. Warn-only survey before the flip found the full blast radius = **4 genuine latent non-exhaustive matches on `ast.Decl` missing the recently-added `WrapDecl`** — the `wrap` feature was landed without updating these Decl-consumers: `iface_codec.encode_ast_decl`/`decode_ast_decl` (iface roundtrip was silently broken for any module with a `wrap`), `analysis_service_driver.collect_decl_names`/`collect_decl_locations` (LSP), and `driver.dump_decl`; all fixed, with a `WrapDecl` iface-roundtrip test. RUN coverage in `tests/stdlib/test_exhaustiveness.spr` (bare-pipeline own-ADT accept/reject) — note `tests/conformance/run/` is executed by NO recipe, so positive fixtures must live under `tests/stdlib/`. **Deferred:** full Maranget usefulness matrix for product exhaustiveness (`(true,true)|(false,false)` on `(Bool,Bool)` is not yet rejected — sound to over-accept per spec §5.5).
- [x] `P2` Change `string.find` return type from `Int` (−1 sentinel) to `Maybe Int`: the sentinel convention is a C-legacy antipattern that contradicts the `Maybe`/`Result` idiom claimed in `docs/haskell-lessons-learned.md` §5; updated all callers in `string.sprout`, `http_server.sprout`, `repl.sprout`, `formatter.sprout`, and `tests/stdlib/test_string.spr`; bootstrap seed refreshed.
- [x] `P3` **String literals lack a byte-length header → `str_byte_len` was O(n) for them. DONE — every Sprout String is now headered; `str_byte_len` is O(1) universally.** Landed as a two-phase, bootstrap-critical layout migration. **Phase A (codegen):** `ir_lowering.emit_str_global` emits each literal as a header-prefixed CSTR block `{ i64 header, [N x i8] bytes }` and the IRStrConst use-site GEP points the String value at the bytes field (payload-8 = header); seed refreshed to fixed point. **Phase B (runtime):** `intern_string` header-prefixes its buffer (the single chokepoint that also covers the previously-bare `env_get`/`argv_get`/`map_nth_key`/`native_set_to_list`/`term_read_key` producers, all routed through it), and `str_byte_len` now reads the header at payload-8 directly, dropping the `sprout_heap_lookup` arena-membership check. The total-header invariant (no bare String reaches `str_byte_len`) is enforced by `SPROUT_GC_HDRCHECK=1`, under which the full suite + example canary pass with zero aborts. **`length` (codepoint count) deliberately stays O(n)** (`sprout_utf8_codepoint_count`) — matches Rust/Go/Swift; O(1) codepoint length over UTF-8 needs a fixed-width encoding (rejected) or a cached counter (not worth the per-string word for a rarely-hot, often-wrong-vs-graphemes number). Tests: `tests/stdlib/test_byte_length.spr` (literals incl. multi-byte, concat, slice, interned map keys), `tests/stdlib/test_ir_codegen_strings.spr` (headered-struct + struct-GEP golden). See `docs/cross-language-design-lessons.md` §0/§D and `docs/runtime-invariant-confidence-v0.md`.
- [x] `P2` Revisit `parse_int` partiality. **DONE (W8/P1, D5).** `parse_int` is now total pure Sprout (`String -> Maybe Int`, `stdlib/prelude.sprout`); callers in the parser, lexer/LSP header parsing, http_server, and scram consume the `Maybe`. Dead `split_ints` deleted. `iface_codec` keeps a local `atom_to_int` to avoid the old panic-on-invalid contract.
- [ ] `P1` **Int overflow policy (OPEN / DEFERRED 2026-07-06).** Native `+`/`-`/`*` currently
  do defined two's-complement wrap (plain `add/sub/mul i64`, no `nsw` —
  `ir_lowering.sprout` `IRIAdd`/`IRISub`/`IRIMul`, re-verified 2026-08-18; the entry's old
  `codegen.sprout:2106` citation is dead, that file was deleted 2026-07-12 when typed-IR
  became the sole codegen path); safe but silently wrong, contradicting the safety-first identity and
  the spec's stated arbitrary-precision `Int` intent (§6.5/§8.4 flag i64 wrap as temporary).
  Decision: **Option A** trap/panic on overflow (Swift/Rust-debug/Zig-safe model; reuses W7's
  `IRPanic` + `.with.overflow` intrinsics, forward-compatible with the bignum end-state) vs
  **Option B** Go model (silent runtime wrap, compile-error on literals only). Author recommends
  A. Full findings + verified prior-art survey: `docs/int-overflow-policy-decision.md`.
  **Blocks W9/X4** (integer-literal overflow) and couples to W7's deferred `INT_MIN / -1` guard.
  **X4 must rule on RADIX literals explicitly (added 2026-08-17).** `0x`/`0b` literals landed
  with `stdlib.bits`, and an over-range one wraps to the low 64 bits read as signed —
  `0xFFFFFFFFFFFFFFFF` is `-1`, `0x8000000000000000` is `INT_MIN`, matching what decimal already
  does (`parse_int` wraps rather than failing; both verified by running them). That wrap is the
  *useful* reading for masks, and the only one under which every 64-bit pattern is writable, so a
  naive "the value must fit in `Int`" rule applied uniformly would **reject the all-ones mask
  idiom**. Decide the radix case on purpose, not as a side effect. Pinned meanwhile by
  `tests/stdlib/test_int_literals.spr`; rationale in `docs/bitwise-int-ops-v0.md` §5.6.
- [~] `P2` **Refutable `let-else` + pure monadic binding.** One binding construct in capability
  tiers. **Tier 1 LANDED 2026-07-06/07:** `let <pat> = <e> else <expr>` layout-sequenced in pure
  expression position, desugared to nested `match` entirely in the parser (mandatory else → always
  exhaustive → no refutability analysis yet); spec §5.2.1; `tests/stdlib/test_let_else.spr`.
  **Tier 1b LANDED 2026-07-27:** residual-binding else `else <rpat> -> <handler>` — the residual is a
  full pattern spliced verbatim into the fallback arm (`else Err msg -> fmt(msg)` names the payload; a
  bare var binds the whole scrutinee; no failure ctor injected). Parser-only (disambiguate on `->`);
  W5 self-enforces residual exhaustiveness; `staircase-of-doom` lint now points payload chains at
  binding-else. Spec §5.2.1 + plan §6b; tests + `tests/conformance/type_error/let_binding_else_nonexhaustive_residual`.
  **Effectful-RHS (do-local) LANDED 2026-07-27:** inside a `do` block, both `<pat> <- <e>` and
  `let <pat> = <e>` accept the same refutable `else`; parse-time desugar into `__t <- e; match __t | …`
  (bind) or `match e | …` (let), no downstream/typing change. Surface **(A)** — `do`-local, keeping
  `let..in` always-pure — chosen over widening `let..in`. Design `docs/effectful-let-else-v0.md`;
  spec §5.2.2; `tests/stdlib/test_effectful_let_else.spr`; verified faithful by converting `infer_range`
  → stage-3 self-host, then reverted. **Remaining tiers:** 2 no-else propagate for `Result`/`Maybe`
  (a built-in `?`); 3 general monad-generic propagate (entangled with effect-system design D2). Prior
  art (verified): Rust `let-else`/`?`, Swift `guard`, Haskell `do`/MonadFail, OCaml binding operators.
  Plan + staging: `docs/let-else-and-monadic-binding-plan.md`.
- [x] `P3` **Sweep `infer.sprout` do-block cascades — DONE 2026-07-28.** `infer_range` and
  `check_fn_body` (:3430/:4523) flattened onto effectful-RHS let..else; behaviour-preserving, guarded
  by the self-host fixed point (both are in the compiler's own closure) + the inference suite; added
  the previously-missing `range_needs_int` conformance test. `infer:1668` (`resolve_obligation`)
  deliberately NOT converted — it is an inverted first-success-wins alternatives chain (descend on
  `Nothing`, terminal on `Just`), the wrong polarity for let..else, in delicate soundness code.
- [ ] `P3` **Sweep the driver staircases (follow-on).** `analysis_service_driver.op_session_update`
  (Tier 1b pure-prefix) and `driver.run_file` (effectful `read_file` head + pure tail). Deferred from
  the infer sweep because neither is in the compiler's closure, so the self-host fixed point does NOT
  guard them — each needs its own behavioural test first: `op_session_update` = export + build a
  `StartupState` (5 refs) + a json request to hit the validator error arms (moderate); `run_file` =
  temp-file + stdout capture (disproportionate for a dump-AST debug driver — consider leaving it).
  Both conversions are behaviour-preserving. The 9 `test_ir_codegen_*` compile-pipeline harnesses are
  also convertible but are deliberately-nested test fixtures — leave them.
- [ ] `P4` **`staircase-of-doom` lint false-positives on inverted alternatives chains.** The rule's
  `classify` treats any chain of 2-arm matches as a flattenable staircase, but an inverted
  first-success-wins chain (continuing branch is a nullary `Nothing`, terminals are value-bearing
  `Just x -> result`) is the wrong polarity for `let..else` and reads worse when forced. Concrete
  case: `infer.sprout` `resolve_obligation` (:1699 as of the existentials PR), flagged `blocked=false`
  but genuinely not a clean fit (it stays flagged, needing `--no-verify` on any commit touching the
  file). Refine the rule to detect the polarity — precise diagnosis (2026-07-30): the false positive
  is an **ambiguous-polarity** chain where *both* branches descend (the terminal branch's body is
  itself a 2-branch `MatchExpr`), so `classify` picks the binding side and the real descent lands in
  the `else`. Candidate fix: in `walk_chain`, do not treat a node as a flattenable link when the
  terminal branch's body `is_two_branch_match` (only `run_file`-style chains with a genuine constant/
  simple terminal stay flagged). **Companion true-positive:** `driver.sprout` `run_file` (:313) is a
  real 3-deep `Result` staircase — flatten it to a `do` + effectful `let..else` (the effectful tier
  landed). Together these two are the *only* reason the binding-annotations (PR #312) and
  existentials (`1504c6d`) commits used `--no-verify`; fixing both makes those files lint-clean. Not
  in `compile_driver`'s closure (driver + lint_rules), so the fix is a seed-fp-ack, not a full reseed.
- [ ] `P4` **Effectful-`let..else` surface (B) — unify `do` and `let..in`.** Shipped as surface (A),
  `do`-local, keeping `let..in` always-pure (`docs/effectful-let-else-v0.md` §5). (B) would let a
  `let..in` block hold `<-` bindings (one flat mixed `=`/`<-` list), at the cost of reclassifying
  `let..in` as sometimes-effectful. Deferred as a possible future unification — revisit only if the
  two-construct split (`do` for effects, `let..in` for pure) proves to be friction in practice.
- [~] `P2` **Binding-level type annotations `let x : T = e`.** Design + prior art:
  `docs/binding-annotations-v0.md`; spec §5.2 (experimental). **Phase 1 LANDED 2026-07-30 (PR #312):**
  **top-level** `let` bindings accept an optional annotation, checked against the initializer via
  infer-then-unify (`apply_let_annotation` in `infer.sprout`, mirroring `check_fn_body`) — no
  checking-mode machinery added. `LetDecl` gained a `Maybe TypeExpr` field (before the trailing
  `SourcePos`); the annotation's head-name threads into `desugar_ctx.desugar_decl_i` so the
  `Vec`/`StringTemplate` literal lowering fires at `let` sites (coercion parity with `fn`
  boundaries); annotation is definition-local, deliberately **not** round-tripped through module
  interfaces. Tests: `tests/stdlib/test_let_annotation.spr`, `tests/conformance/type_error/let_annotation_mismatch.spr`.
  **Deferred follow-ups:**
  - [ ] `P2` **Phase 2 — `let…in` and `where` binding annotations.** Those bindings have no
    dedicated binding AST node to hang an annotation on (`let…in` at `parser.sprout:655`/`686`,
    `where` at `:1219` are desugared), so annotating them is materially more invasive than the
    `LetDecl` field. Specify separately when undertaken (`docs/binding-annotations-v0.md` §5).
  - [ ] `P3` **do-block `let`/`<-` step annotations.** `DoLetStep String Expr` (`ast.sprout:54`)
    has the same shape as `LetDecl` and could carry a `Maybe TypeExpr` field the same way; deferred
    with Phase 2 since the two share the "annotate a non-top-level binding" surface.
  - [ ] `P2` **Anonymous `any C` introduction — `let row : List (any C) = …`.** Blocked on the
    existential/GADTs arc (`docs/gadts-v0.md` §6): a type-directed rewrite that boxes each element
    into a per-value dictionary needs inferred types, so it cannot ride the Phase-1 syntactic
    coercion. Belongs to that arc, not this one.
- [~] `P2` **Existentials / GADTs (staged).** Analysis + verified prior art + staging + T-shirt
  estimate: `docs/gadts-v0.md` (non-normative). The insight: existentials need none of full
  GADTs' four hard prerequisites (local per-branch equalities, mandatory signatures, bidirectional
  checking, constraint-aware exhaustiveness) — they introduce exactly **one** new mechanism, a
  rigid/skolem variable that must not escape a single match branch. Staging:
  - [x] `P2` **Stage 0a — unconstrained existentials (M). LANDED 2026-07-30.** Constructor-level
    `exists` prefix (`| exists a. Boxed a`) binds a type var absent from the head, hiding the field
    type. Skolem = a fresh rigid `TConst` (`$sk<n>`) minted on the pattern/unpack path
    (`unifier.instantiate_ctor_pattern`); the existing nominal `unify(TConst,TConst)` rule enforces
    soundness for free (unifying a skolem with a concrete type or a distinct skolem fails), so the
    escape check is a pure diagnostic (`type_mentions_skolem` → "escapes its scope") with no
    bespoke soundness code. Existential vars derived as `scheme.type_vars \ ftv(result)`; **zero
    runtime change**. Spec §5.6 (experimental). Tests: `tests/stdlib/test_existential_0a.spr`,
    `tests/conformance/type_error/existential_{escape,pin,merge}.spr`.
  - [x] `P3` **Stage 0b — constrained existentials via `any C` (M actual; est. L). LANDED
    2026-07-30, scoped to single-method superclass-free classes.** `| Shown (any ToString)` desugars
    (parser) to a fresh existential var + a `ToString` constraint on the ctor. Construction routes
    through the existing `where C a` dict-injection path (`inject_constrained_fn_dicts_via_field`) —
    the ctor scheme's constraint field carries `(var, class)`, so `Shown(v)` requires+resolves the
    instance and **packs the method function-pointer(s)** as hidden `'p'` ctor fields (ctor
    arity/fks grow by the witness count). Unpack seeds a skolem-keyed `@fwd` given marker whose VALUE
    is a stable `$ex_<var>` forwarding identity (`types.existential_fwd_head`), shared by infer and
    lowering so resolve's EvForward key and lowering's `ctx_fwd` seeding agree with **no drift and no
    body scan**; a class method on the unpacked value then forwards the packed witness. **No reified
    dict struct was needed** — the per-method pointers ARE the dictionary, redirected into the heap
    value (the residual devirtualization leaves behind, `docs/gadts-v0.md §5.2`). Missing-instance
    construction rejected at the site ("No instance…"). Spec §5.6; example
    `examples/existential_render.sprout`; tests `tests/stdlib/test_existential_0b.spr`,
    `tests/conformance/type_error/existential_missing_instance.spr`.
    - [x] `P3` **Multi-method / superclass existentials (witness-slot enumerator). LANDED
      2026-07-31.** The crude one-witness-slot-per-constraint count was exact only for a single-method
      superclass-free class. Generalized via ONE shared enumerator `types.witness_slots_for_class`
      (`class_with_transitive_supers` × `class_methods_for`, matching resolve's dict expansion),
      called from BOTH `ast_to_ir.witness_slot_count` (ctor-field count) and lowering's unpack, so
      count/pack/unpack cannot drift. Lowering now binds one `$wtns_<var>_<class>_<method>` per slot
      and seeds a `ctx_fwd` block per effective class under its own key; `infer.seed_from_skolem`
      seeds `@fwd` givens for the direct class AND each superclass (via `seed_superclass_fwd_markers`)
      so an inherited method forwards. Enabler: `lowering.lower_decl` now RETAINS `ClassDecl`s in the
      lowered stream (inert — byte-identical IR for all non-existential programs) so `build_ctor_table`
      can rebuild `class_order`/`super_map` post-lowering. Guard (`check_existential_constraints`)
      dropped. Tests: `tests/stdlib/test_existential_{multimethod,superclass,module_qualified}.spr`.
      Also closed a documented pre-existing gap this surfaced: `bundler.qualify_ctor_args` was
      passing a ctor's `exist_constraints` through UNQUALIFIED (its own comment said "Stage 0b must
      qualify their class names here"), so an `any UserClass` in a file with a `module` header kept a
      bare class name while instances/class-markers were qualified — construction and unpacked-value
      dispatch then missed (`@inst`/`@fwd`). Now qualified via `qualify_constraints`; inert for
      non-existential programs (byte-identical) and for the seed (the compiler sources use no `any C`).
      - [ ] `P4` **Imported-class existential (`any some_import.Renderable`) untested.** Every case
        exercised defines the boxed class in the SAME module. A boxed imported class routes through the
        same `qualify_constraints`/alias tables as an imported `where` constraint, so it likely works,
        but add a cross-module test to confirm.
      - [x] `P4` **Unify resolve's traversal copy. LANDED 2026-07-31.** `resolve.sprout` kept its own
        byte-identical `class_with_transitive_supers` (the PACK authority for evidence order) while
        `types` owned the count+unpack copy — a silent-drift surface whose failure mode is a
        wrong-method dispatch, not a loud error. Both PACK call sites now use the shared
        `types.class_with_transitive_supers`; the local copy + its `collect_supers_dfs`/`prepend_supers`/
        `append_str_list`/`list_str_member` helpers were deleted. Behavior-preserving (the two copies
        differed only in `list_str_member` vs generic `list_member` over a `String` list). Added
        `tests/stdlib/test_existential_superclass_module.spr` — the previously-uncovered intersection of
        superclass + `module`-qualification — which passed on master (coverage gap, not a bug) and now
        guards the qualifier agreement. (`collect_super_map` is the map BUILDER, not the traversal, and
        stays local per its consumer comment.)
      - [ ] `P4` **`validate_ctor_where` only checks the FIRST constraint arg.** `parser.constraint_var_name`
        matches `TypeConstraint _ [TypeName v | _]` and validates only `v`, so a where-clause like
        `where Convert a b` with `b` unbound by the `exists` prefix is accepted (only `a` is checked).
        Latent today (multi-parameter classes are unsupported, so such a constraint fails elsewhere), but
        the validator's stated contract — every constraint applies a class to an `exists`-bound variable —
        is not upheld for the 2nd+ argument. Fix: validate every `TypeName` argument of the constraint is
        exists-bound (reject non-`TypeName` args as today). Add a `parse_error` fixture. Surfaced by the
        retroactive `/code-review` of PR #3.
      - [ ] `P4` **`check_existential_constraints` is a dead no-op hook.** PR #2 emptied its body to
        `Nothing` (the multi-method/superclass guard it enforced is now supported), but it is still
        called from `infer.sprout` — a pass that can never return an error. Either wire a real future
        existential-declaration check here or drop the call and keep the comment as the home marker.
        Cosmetic; surfaced by the retroactive `/code-review` of PR #2.
      - [ ] `P4` **Extend constraint-head class validation to instance/class positions.**
        `infer.validate_constraints_all` now rejects a variable-first `where a ToString` on a
        **`FnDecl`** (head must be a declared class; swap-aware hint). The same swap on an
        `InstanceDecl` context-constraint or a `ClassDecl` superclass (`class Ord a where a Eq`)
        is not yet validated and would silently drop the constraint. Reuse
        `collect_declared_class_names` + `validate_fn_constraint` over those positions.
      - [x] `P3` **Reject a `where` constraint whose type variable is not in the signature. LANDED.**
        `fn tostr(x) where ToString x` (well-ordered, but `x` is a *value* parameter, not a
        signature type variable) passed `--phase check` yet lowered to an undefined
        `@to_string` call — the same silent-drop → link-failure the order-swap fix targets,
        reached via a phantom constraint var. `validate_fn_constraint.check_constraint_tyvars_in_sig`
        now rejects any constraint whose argument mentions a type variable absent from
        `fn_sig_tyvars` (the diagnostic points to annotating the parameter). The flip exposed one
        genuinely ill-typed lowering fixture (`test_lowering`'s `where MyShow a, MyShow b` with `a`
        unused), fixed by giving `a` a real signature slot. Fixture: `constraint_phantom_tyvar`.
    - [x] `P3` **Explicit constrained-prefix form + constraint keyword. LANDED 2026-07-31.**
      Constraint keyword decided: **`where`** (not `=>`), matching every other Sprout constraint site.
      Syntax `| exists a. Shown a where ToString a` (multiple constraints comma-separated). Parser:
      `parse_ctor_where_clause` reuses `collect_constraints`, appended to `exist_constraints`;
      `validate_ctor_where` rejects a `where` with no `exists` prefix or a constraint var not bound by
      it (parse-time, blocks Haskell-style DatatypeContexts). Because item 1 (witness-slot enumerator)
      already made the backend constraint-list-driven, the new surface was mostly parser wiring — with
      one real backend fix: a constrained var appearing ONLY nested in a compound field
      (`Bag (List a) where Describe a`) never seeded its unpack given (both infer's
      `field_pattern_for_var` and lowering's `field_var_for_constraint` matched a field whose type IS
      the bare var). Now both select the field via the shared `list_member(var, types.ftv(pt))`
      "mentions" predicate (so their `$ex_<var>` forwarding heads still agree), and infer's new
      `skolem_for_var_in` co-walks the declared field type against the unpacked skolem type to recover
      the skolem from within `List $sk`. The flagship multi-field shared-state `Widget`
      (`docs/gadts-v0.md §5.1`) already worked from Stage 0a. Tests:
      `tests/stdlib/test_existential_prefix{,_compound}.spr`,
      `tests/conformance/parse_error/ctor_where_{on_head_param,unbound_var}.spr`.
      - [ ] `P4` **Ambiguous existential construction → opaque arity error.** Constructing an
        existential whose type is undetermined — an empty compound container, `Bag([])` — leaves the
        constraint's var a free tyvar, so no witness is injected at construction and it surfaces
        downstream as `ast_to_ir: ctor application has wrong arity`. It IS rejected (fails loudly), but
        the message is opaque. Inference should detect the unresolved-tyvar-head existential constraint
        at a top-level construction (no forwarding given in scope) and emit a located "ambiguous
        existential: cannot determine which `C` instance to pack" error instead. New sharp edge unique
        to compound-field existentials (`any C`'s single value field is always determinate).
      - [ ] `P4` **First-mentioning field wildcard-bound while a later field binds the var.** Both
        passes select the FIRST field mentioning the constrained var. If that field is wildcard- or
        non-var-bound while a *later* field binds the same var (`exists a. T (List a) a where ToString a`
        matched `T _ x`, then `to_string(x)`), no given is seeded — infer's `seed_one_given` falls to
        `_ -> env` and lowering's `pattern_var_name` returns `Nothing`. Consistent across both passes
        (both skip) and fails loudly with "No instance", so not a soundness hole or silent miscompile,
        and not a regression (the old bare-var path could not express multi-field vars at all). The
        working sibling `T xs x` (first field var-bound) resolves both `xs`-elements and `x` (shared
        skolem, one `$ex_xs` head). Fix: pick the first VAR-BOUND mentioning field, not merely the first
        mentioning one — in both passes, keeping them in lockstep.
  - [ ] `P4` **Stage 1 — index refinement (full GADTs) (XL).** `IntLit : Int -> Expr Int`.
    Deferred behind the local-annotations gate (`docs/scoped-type-variables-analysis-2026-07-26.md`);
    needs an OutsideIn-style local-equality solver + bidirectional checking + constraint-aware
    exhaustiveness. Out of scope for v0/v1.
- [ ] `P2` Revisit string-interpolation type-directed dispatch (Mechanism A): Phase 4 ships a simple syntactic-coercion form (elaborator inserts `template_to_string` only at `String`-expected contexts; default template result is `String`). Evaluate migrating to an `IsTemplate` typeclass with instances for `String` and `StringTemplate` once usage patterns settle. Class-based dispatch is more principled and consistent with the rest of the class system; tradeoff is added constraint-machinery overhead and possible defaulting ambiguity. Decision should be driven by whether a third meaningful instance (e.g. `Bytes`, a logging frame, a tagged-template processor) lands and forces generality.
- [x] `P1` Validate type-name references in `TypeDecl` field and constraint positions resolve to a declared type (strict type-name validation). Landed in PR feat/strict-typedecl-validation: `validate_all_decls` pass in `stdlib/compiler/infer.sprout` runs after `pre_scan_fn_decls`, walks `TypeDecl` constructor fields, `RecordDecl` field types, and `AliasDecl` RHS type-exprs; emits `type-validation: unknown type name \`X\` in declaration \`Y\`` on the first unresolved uppercase `TypeName`. Mutual recursion between ADTs is safe (pass runs post-scan). Positions NOT yet covered (see follow-up below): `ClassDecl` method signatures, `InstanceDecl` constraint types, `FnDecl` param/return annotations.
- [ ] `P2` Extend strict type-name validation to `ClassDecl` method signatures, `InstanceDecl` constraint types, and `FnDecl` param/return annotations (follow-up to P1 above). The `validate_decl` function in `stdlib/compiler/infer.sprout` currently matches `| _ -> Nothing` for these positions. Extend it — the main complication for `FnDecl`/`ClassDecl` is correctly threading local type-parameter sets into `validate_te` so that method-specific variables like `a`, `b` in `fmap(f: a -> b) -> f b` are not flagged.
- [x] `P1` **Silent miscompile: a user top-level name colliding with an imported function's parameter resolves to the user global — FIXED 2026-07-16.** Discovered 2026-07-16 while adding channels (L0.8). Repro: a program defining `fn body(ch) = …` and calling `with_timeout(ms, \_ -> body(ch))` — `with_timeout(ms, body)`'s **parameter** `body` (a `Unit -> a` closure argument) is resolved to the user's top-level `@body` **function** instead of the parameter. The lowering then eta-expands the global (`\a0 -> body(a0)`) and passes THAT as the fork body, discarding the real closure argument — so the captured `ch` is never seen and `body` runs with the unit `0`, crashing (`sprout_tag: null pointer` in the channel case, arbitrary wrong behaviour in general). **ROOT CAUSE (narrower than the original guess — NOT the checker, NOT the `translate_expr` TVar resolver, which orders params before globals correctly): it was closure-conversion's free-variable analysis.** `ast_to_ir.compute_free_vars` excluded any name present in `top_level` (top-level fn + ctor names) from the free-variable set, on the assumption such names are `@`-addressable globals that need no capture. But an enclosing function's parameter that *collides* with a top-level fn name is thereby dropped from the lifted lambda's capture set (`__sprout_ir_lambda_12` captured only `ms`, not `body`); inside the lifted body, `body` is then neither a capture nor a lambda param, so `translate_expr`'s TVar path falls through to `fn_arities` and eta-expands the top-level `@body` (`__sprout_ir_eta_body_13` = `\a0 -> @body(a0)`). **FIX (one line, `ast_to_ir.sprout:31`): delete the `|| set_member(name, top_level)` clause.** `compute_free_vars`'s sole caller (the lambda-lifting site) already runs the result through `filter_capturable` (keep iff in `params ∪ captures`), which drops genuine globals anyway — so the `top_level` exclusion was redundant *everywhere except at a collision*, where it caused this bug. Byte-identical IR for all non-colliding code (seed fixed-point reached at iteration 2, one past the codegen change); also fixes the nested-lambda variant (an outer-lambda capture colliding with a global) for free, which a `top_level`-minus-`params` set-diff would have missed. Regression test `tests/stdlib/test_param_shadows_toplevel_fn.spr` — a **pure, scheduler-free** shape (a HOF param passed in value position inside a capturing lambda + a same-named top-level fn), RED = `1004` (999+5, the global) vs GREEN = `47` (the closure). Same *class* as the `append`/Semigroup collision (BACKLOG item 5 below) but a **distinct mechanism**: that one is codegen dispatching on a source name; this one is free-var analysis excluding a shadowed name. The broader architectural root (canonical identity, item 4/item 5) remains open for the `append` family. `tests/task_io_smoke/timeout_chan_drop.spr` can now safely use `body` as a param name (kept as-is to avoid seed churn; the pure regression test is the guard).
- [x] `P1` Stream IR codegen output per-function instead of materializing the full `IRProgram` in memory. Shipped via a new `stdlib/compiler/ir_pipeline.sprout` streaming orchestrator that runs translate→root→lower per-function with `Ref(List String)` accumulators, so no full `IRProgram` is ever held in memory simultaneously. Output is byte-identical to the pre-streaming path (golden diff: 0 differences). Peak RSS on the 8 `test_ir_codegen_*` tests that import `stdlib.compiler` dropped from ~2 GB to ~240 MB; the 8 files have been removed from `tests/IR_XFAIL` and the OOM skip mechanism removed from `_run-ir-files` in the justfile.
- [ ] `P1` Typed-IR `@print` call site dispatch. Surfaced 2026-06-18 during PR 70 audit. Typed-IR emits `call i64 @print(i64 %x)` for every call to prelude's `extern fn print(val: a) -> Unit !{IO}`, and `ir_lowering.sprout`'s hardcoded `declare i64 @print(i64)` exists — but **`@print` is not defined in `runtime/sprout_runtime.c`**. Programs compiled via `--use-ir-codegen` that call `print()` (factorial, fizzbuzz, etc.) **fail to link** with `Undefined symbols: _print`. The bug has been masked because `run-example-canary-ir`'s `_run-ir-files` recipe only runs `opt --passes=verify` on the emitted IR — it does not link or execute the binary, unlike its direct-codegen counterpart `run-example-canary` (which is gated by AGENTS.md DoD #11 to run to completion). Direct codegen handles this at the AST level: `emit_call` (codegen.sprout:2434) special-cases `TVar "print"` and dispatches in `emit_print_call` (codegen.sprout:2474) to `@print_str(ptr)` / `@print_value(i64)` based on the runtime type of the argument. The fix in typed-IR is to add equivalent dispatch in `ast_to_ir.sprout`'s `translate_direct_call` (or wherever `TVar "print"` reaches codegen). Two follow-up items: (a) extend `_run-ir-files` to optionally link + run via the same flag set as `run-example-canary` so this class of bug surfaces in CI; (b) decide whether `eprint` (which has a runtime bridge function at `sprout_runtime.c:1270` for "old stage-1 codegen") needs the same audit.
- [ ] `P2` `codegen.sprout`'s `@str_compare` latent ptr/i64 ABI mismatch. `codegen.sprout`'s `emit_ptr_comparison` (direct-codegen path, ~line 2093) declares and calls `@str_compare(ptr, ptr)`, but the C runtime signature is `long long str_compare(long long, long long)` — the same ptr/i64 mismatch that PR 16.1 fixed for the `analysis_*` family. On current arm64/x86-64 targets pointers and i64 occupy the same register so the mismatch is harmless at runtime; it would be caught by strict LLVM type verification. The typed-IR path (`ir_lowering.sprout`'s `IRStrCompare` lowering) correctly uses `declare i64 @str_compare(i64, i64)` per the C signature. Fix `codegen.sprout` to match: use `ll_i64()` for both params in the `str_compare` intrinsic-table entry and remove the `inttoptr` coercions at the call sites in `emit_ptr_comparison`. Latent — not a correctness bug on current targets, but should be fixed before `codegen.sprout` survives past PR 13.
- [x] `P1` Typed-IR vs direct codegen architectural ABI gap (tuples and closures) — RESOLVED 2026-07-12 by retiring the direct backend (typed-IR is now the sole codegen path, so there is no cross-path ABI boundary to diverge; the differential check was deleted with `codegen.sprout`). Surfaced 2026-06-18 during PR 70 differential corpus expansion. Direct codegen passes tuples as anonymous LLVM struct types (e.g. `define i64 @sum_coords({ i64, i64 } %point)`) and closure envs as `ptr`. Typed-IR uniformly uses i64 for both (`define i64 @sum_coords(i64 %point)`, `define i64 @map(i64 %_, i64 %_)`). Both representations are internally consistent within each codegen path, but they are not interchangeable — calling a typed-IR-compiled `@map` from a direct-codegen caller (or vice versa) would crash at the ABI boundary. This is not a bug to fix in isolation: PR 11 (dispatcher rewire) makes typed-IR the sole codegen path, eliminating the cross-call problem. Until then, the differential check's corpus must be limited to tiny single-purpose files (`scalar_arithmetic_demo`, `hello`, `factorial`, `fizzbuzz`) that do not exercise tuple/closure constructs. Adding stdlib-importing files generates 60+ false-positive divergences per file. After PR 11, the differential check can either (a) be retired, (b) be expanded to compare typed-IR output against a frozen snapshot, or (c) be repurposed as a regression gate against unintended ABI changes within the typed-IR path itself.
- [x] `P1` Typed-IR GC-rooting gap: `IRCall` results never rooted across later allocating calls (root cause of the post-Result-do-bind `non-exhaustive match` aborts). Surfaced 2026-06-19, **fixed 2026-06-21 via Option B**: `IRCall`'s vestigial `ret_ty: String` (always `"i64"`) replaced with `kind: IRType`, populated from the callee's Sprout return type via new `type_kind.type_is_non_heap_scalar` (Int/Bool/Char → IRTScalar, else conservatively heap); `ir_rooting.op_produces_simple_heap` now roots IRCall results unless IRTScalar. Regression test `tests/stdlib/compiler/test_ir_call_result_rooting.spr` (heap call result live across a later call gets a root; scalar twin does not — verified RED with the IRCall case reverted). Parity burn-down OK 87→89, TYPED-RUNTIME 11→9, TYPED-COMPILE flat at 8, zero regressions. Of the 5 `non-exhaustive match` files, `nested_int_bool` fully cleared to OK; `string_pattern`/`unit_ctor_field` relocated to exit-1 partial-failure and `closures`/`match` to a fresh exit-139 — the next typed-codegen layer (see follow-up below). Five `test_ir_codegen_*` backend-importing files (nested_int_bool, string_pattern, unit_ctor_field, closures, match) abort at runtime with `runtime error: non-exhaustive match`; two more (nested_patterns, range_and_intrinsics) exit 139. ROOT CAUSE (lldb + IR evidence on nested_int_bool): the abort is in `stdlib.test.assert_true`'s single-ctor `match state` — at the 2nd call `state` (a `TestState` from `state <- new_state()`) has the SAME pointer but a corrupted tag (use-after-free). In `__sprout_user_main` typed IR, `%t0 = call @new_state()` is **never** `sprout_gc_push_i64_root`'d, yet is held live in an SSA register across four heavily-allocating `translated_ok_contains` calls → GC reclaims it. The gap: `ir_rooting.op_produces_simple_heap` (ir_rooting.sprout:68) has **no `IRCall` case** → falls to `| _ -> Nothing`, so call results never enter `heap_origin` and are never rooted across triggers. `IRCall`'s `ret_ty` field is always `"i64"` (uniform ABI), so heap-ness is not recoverable in the rooting pass — it must be captured at translate time in ast_to_ir. Two fixes: (A) conservative — `| IRCall r _ _ _ -> Just(r)` (safe, but over-roots every scalar-returning call, contradicting the deliberate scalar-elision at line 87); (B) typed (preferred, matches IRGetField/IRLoadEnvSlot) — add an `IRType` kind field to `IRCall`, populate from the callee's Sprout return type in ast_to_ir, honour it in op_produces_simple_heap. `scripts/ir_runtime_parity.sh` is the burn-down gate (was OK 87 / TYPED-RUNTIME 11 / TYPED-COMPILE 8; now OK 89 / TYPED-RUNTIME 9 / TYPED-COMPILE 8 after the fix).
- [ ] `P1` Typed-IR next-layer crashes after the IRCall-rooting fix (P11-2d follow-up). With Result-do-bind (P11-2c) and IRCall rooting (P11-2d) landed, 9 corpus files still diverge typed-vs-direct: backend-importing `test_ir_codegen_{closures,match,nested_patterns,range_and_intrinsics}` exit 139, `test_ir_codegen_{string_pattern,unit_ctor_field,ctors}` exit 1 (value miscompile, no crash), and `tuples.sprout`/`06_tuple_param.spr` render a raw pointer instead of a tuple value. 8 TYPED-COMPILE files (aoc_2025_day_1/2, astar, deriving/eq/to_string, test_string, test_to_string) emit opt-verify-failing or empty IR — a separate compile-time class. **P11-2e characterization 2026-06-22 (exit-139 cluster):** `range_and_intrinsics` and `closures` crash at the IDENTICAL site — `sprout_intern_hash(s="")` ← `intern_string` ← `map_set(map_h=0, key_val=<garbage ptr>, value=…)` ← `dict_set` ← `dict_from_list_loop` ← `dict_from_list` ← `checker.typecheck_typed + 528`, i.e. building `dict_from_list(builtin_entries() ++ extra)` (checker.sprout:68) with a GARBAGE first-tuple-field (the dict KEY). `nested_patterns` crashes in `strcmp` (garbage string), `match` in `vector_get(vec=<garbage>)` — likely the same value-corruption root, different deref. **NOT a rooting bug**: crashes identically with `SPROUT_GC_THRESHOLD` huge (GC off) AND =1 (GC stress) → deterministic VALUE miscompile, garbage depends on heap layout (uninitialized/wrong-offset read). `builtin_entries()`→`analysis_entries()` (checker.sprout:738) is a large list-LITERAL of `(String, types.Scheme)` tuples with deeply-nested `bt_*` constructor values. **Minimal-repro method does NOT converge here** (unlike P11-2c/2d): `dict_from_list` of `(String,Int)`, list-literal `(String, Maybe Int)`, and nested-ADT-value tuples all PASS on the typed path — the trigger needs analysis_entries' exact scale/shape. NEXT METHOD: IR-differential of the actual `analysis_entries`/`builtin_entries`/`dict_from_list` functions (`--emit-ir-one` both paths, diff) to find the structural divergence, rather than growing a synthetic repro. Same bisection otherwise.
- [x] `P1` Typed-IR GC-rooting gap #2 (P11-2e) — FIXED 2026-06-24 (commit 207e144, branch `fix/p11-2e-operand-tuple-rooting`). **This RESOLVES the exit-139 cluster above and CORRECTS its characterization: it WAS a rooting bug, not a value miscompile.** The "NOT a rooting bug" conclusion (drawn from `SPROUT_GC_THRESHOLD` toggling) was wrong — `SPROUT_GC_THRESHOLD=huge` does NOT disable the collector, so "GC off" never held; an **lldb single-run free-trace** found the actual premature free. The `dict_from_list(builtin_entries() ++ extra)` garbage KEY was exactly this: the `(key, scheme)` tuple operand swept during the tuple/make allocation. TWO entangled root causes: (1) **store-after-alloc operand exposure** — `@sprout_makeN` (IRMakeCtor), `@sprout_alloc_closure_env` (IRMkClosure), `@sprout_alloc_tuple_blob` (IRMakeTuple) allocate (a GC point) then store unprotected operand registers, unlike `@str_concat` which `SPROUT_HANDLE`s operands first; their heap operands were swept mid-build. (2) IRMakeTuple/IRGetTupleField results never heap-classified, so tuple values were never rooted. FIX in `ir_rooting.sprout`: `op_exposes_operands` + `roots_across` (live-after ∪ heap-operands for that subclass); `IRMakeTuple` → `op_triggers_gc`; `IRMakeTuple`+`IRGetTupleField` → `op_produces_simple_heap`. Plus an exposed regression: synthetic `__sprout_init_globals` (and its lifted partial-app wrappers) were absent from `idx_map` → root-slot names collided with body temps (`%t0`); `ast_to_ir.synthesize_init_globals_fn` now returns `next_idx`, both streaming (`--use-ir-codegen`) and batch paths register it. Tests: `test_ir_rooting` GREEN under `SPROUT_GC_STRESS=1` + default; new T6–T9 (ctor/closure/tuple operand + tuple-extract live-across); T5 and `test_ir_codegen_{ctors,match}` expectations corrected (encoded the pre-fix under-rooting). Parity OK 89→100, TYPED-RUNTIME 9→2, TYPED-COMPILE 8→4, zero new reds. Remaining open (pre-existing, NOT rooting): tuple VALUE-RENDERING (`tuples.sprout`/`06_tuple_param.spr` raw ptr) + deriving/eq/astar TYPED-COMPILE class. Writeup: `docs/archive/p11-2e-PR-A-rootcause-2026-06-23.md`.
- [x] `P1` test-stress false-greens (PR 11 item 4) — FIXED 2026-06-25 (branch `fix/gc-stress-ctors-match`). `test_ir_codegen_{ctors,match}` crashed under `SPROUT_GC_STRESS=1` (`non-exhaustive match`); `closures` crashed `EXC_BAD_ACCESS`. All three were ONE class: the typed path (`ir_rooting.sprout`) did NOT root heap **operands of an `IRCall`** across the call. `@ref_new` calls `sprout_gc_maybe_collect_threshold()` and allocates the RefVal BEFORE `r->value = value`, so an unrooted heap operand is swept and a dangling pointer is stored; a later `sprout_tag` read (ctors/match) or wild deref (closures) hits the reused/garbage object. The old comment wrongly assumed "calls hand operands to a callee that roots its own params" — true for Sprout callees, FALSE for C builtins that alloc-before-store. **Root-cause method:** new env-gated runtime instrumentation `SPROUT_GC_LINEAGE=1` (poison-on-free + retain + free-backtrace stashed in the corpse, aborts at the `sprout_tag` read of a poisoned ptr) named the free site directly: `sprout_gc_collect_with_reason ← ref_new ← __sprout_ir_lambda_119`. Differential proof: direct `--emit-ir` roots `ref_new` operands (`push_i64_root`/`pop_roots(1)` bracketing the call); typed did not. **FIX:** `ir_rooting.op_exposes_operands(IRCall) → true` (was `false`) — roots in-scope heap operands across every call, matching `op_triggers_gc`'s "every call may allocate" conservatism and the direct path. Purely additive (only adds roots; cannot create a UAF). Regression: `test_ir_rooting.spr` T11 (IRCall heap operand dead-after-call → 1 root; verified RED at 0 roots pre-fix). `test_ir_codegen_{ctors,match,closures}` promoted from `STRESS_XFAIL` to gated `STRESS_FILES`; all green under `SPROUT_GC_STRESS=1`. Writeup: `docs/archive/p11-item4-gc-uaf-handoff-2026-06-25.md`.
- [ ] `P2` GC-safety tooling follow-ups (from the P11-2e retrospective; #1–4 landed on `chore/gc-safety-tooling`: `just test-stress`, exhaustive op-property classifiers, `SPROUT_GC_DISABLE`, `scripts/gc_free_trace.py`):
  - **(5) Single source of truth for operand protection.** `ir_rooting.op_exposes_operands` is a hand-maintained list that can drift from runtime reality. Either make ALL allocating builtins `SPROUT_HANDLE` their operands (so the rooting pass needs no per-op exception and the "operands are protected" assumption becomes universally true), OR have each allocating runtime fn declare the property and have the pass read it.
  - **(6) GC-safety linter at the IR-rooting layer.** `scripts/gc_safety_check.sh` checks only the C runtime (unrooted C locals across `maybe_collect`). Add an analog that models "this IR op allocates-then-stores → its heap operands must be rooted" over `ir_rooting`'s output, to catch this class statically (the bigger, enforced version of the exhaustive tables).
  - **(7) Collision-proof root-slot naming.** Root temps use `%t<idx>` from `idx_map`; a missing entry collided with body `%t0` (the init-globals regression). Use a reserved namespace (e.g. `%root.N`) so `idx_map` gaps CANNOT collide, or add a lowering-time duplicate-local-name assertion (catches it before `opt --passes=verify`, with a clearer message).
  - **(8) Parity gate honesty.** Generalize the golden mode (added on `fix/typed-tuple-print-rendering`); make `ir_runtime_parity.sh` LOUDLY flag "direct produced 0 bytes / crashed → invalid reference" instead of silently treating direct as ground truth; and wire a curated parity (or the golden subset) into CI so the burn-down can't silently drift. **2026-06-24 review confirmation:** the golden-mode half has landed, but `ir_runtime_parity.sh` is still NOT invoked by any `.forgejo/workflows/ci.yml` step (`grep -n parity .forgejo/` matches comment lines only) — so `06_tuple_param.spr.out` is a LOCAL-ONLY regression guard. The CI-wiring half is the outstanding piece and now has a concrete first consumer. **2026-06-26 update (branch `ci/test-ir-required`):** the `test-ir` job is now a REQUIRED gate on PRs + master (`continue-on-error` removed, PR-exclusion `if` dropped). This blocks typed-codegen COMPILE/CRASH regressions — but NOT output-parity regressions: `run-example-canary-ir` only checks exit 0, so a wrong-but-exit-0 render (the `tuples.sprout`/#93 class) would still pass. Wiring `ir_runtime_parity.sh` (or its golden subset `tests/golden/runtime/*.out`) into a CI step is the remaining piece to make the 109/109-OK / 0-TYPED-* parity result CI-enforced rather than local-only.
- [x] `P2` Typed-IR tuple value-rendering — FIXED 2026-06-26 (branch `fix/typed-tuple-print-tostring`). ROOT CAUSE: `print` is a structural runtime renderer (`print_inline_value`) — type-erased, so a tuple element that is a `Bool` printed as `1` and a STATIC string literal (`tuples.sprout`'s `describe(...)` result) printed as its raw (ASLR-dependent → non-deterministic) heap pointer; static strings aren't GC-tracked so the renderer can't tell them from `Int`. **FIX (type-directed, at inference altitude):** `infer_print_call` (infer.sprout) peeks the single `print` argument's type; when it is a tuple, it rewrites `print(t)` → `print(to_string(t))` so the tuple's `ToString` instance renders each element via its own instance (nested tuples/Bool/String all correct). Inference then resolves the (possibly nested) element dictionaries for free, and **BOTH codegen paths inherit the fix** (direct codegen no longer drops `print(tuple)` — it now routes through `to_string`→`print_str`). `tuples.sprout` → `((true, 7), 42, positive tuple)`, `06_tuple_param` → `(true, 7)`, identical on both paths. Goldens: new `tuples.sprout.out`; `06_tuple_param.spr.out` flipped `(1, 7)`→`(true, 7)`. `examples/tuples.sprout` given a `module main` header so the prelude (hence the `ToString (a,b,c)` instances) is bundled — bare files still get NO prelude, so `print(tuple)` there is a loud link error (undefined `@to_string`), consistent with the importless-self-contained design. `print` stays an **intrinsic** (NOT constrained with `ToString`); only tuple *arguments* are rewritten, so `print(Int)`/`print(String)`/`print(true)` are unchanged (importless-safe, bootstrap-safe, spec-neutral). Parity 109/109 OK, **0 TYPED-RUNTIME / TYPED-COMPILE / TYPED-LINK** (the runtime-parity gate is now clean — typed-codegen-flip unblocked from the parity side). Follow-ups: the full `print`-through-`ToString` redesign and the prelude-bundling-default reconsideration are tracked as `P3` items below.
- [ ] `P3` Reassess the `print` design — should compiled `print` dispatch through `ToString` everywhere (matching the REPL), instead of the type-erased runtime renderer (`print_inline_value`)? Raised 2026-06-26 alongside the tuple-rendering fix, which routes ONLY tuple-typed args through `to_string` (surgical) rather than constraining `print` itself. The full redesign (`fn print(val: a) -> Unit !{IO} where ToString a = print_str(to_string(val))`, deleting the runtime renderer) is the principled end state but was deliberately deferred: it (1) **regresses the #92 importless loud-fail** — `print` resolves in importless files only because it is a compiler intrinsic; demoting it to a constrained stdlib fn makes every importless `print(...)` an "unresolved call"; (2) **risks bootstrap** — every `print` call in `stdlib/compiler/` would need a `ToString` instance in scope or `refresh-seed` wedges; (3) is a **normative spec change** — flips `print(true)` from `1` to `"true"` globally (documented behavior at `sprout_runtime.c:1285`). Requires its own design doc (Design Change Process), a compiler-wide `print`-site/instance audit, and a `docs/spec-v0.md` revision. NOT to be folded into a parity task. Decision needed: do it, or formally bless the intrinsic+surgical-rewrite split as the permanent design.
- [ ] `P3` Reconsider the prelude-bundling default (polarity + trigger). Raised 2026-06-26. Today `bundler.collect_modules` (bundler.sprout:503) bundles the prelude iff `any_has_module_name(rev_mods)` is true — i.e. iff some collected module has a non-empty `module` header (or an import pulled one in). A **bare** file (no `module` line, e.g. `examples/maybe_map.sprout`) gets NO prelude, so it can redefine `Maybe`/`map`/etc. without colliding with the stdlib. Two concerns: (1) **polarity** — the prelude is opt-IN by accident of syntax; the conventional beginner-friendly design is implicit-prelude-ALWAYS with an explicit opt-out (Haskell `NoImplicitPrelude`, Rust std prelude, Python builtins). The most natural first program `fn main() -> Unit !{IO} = print((1, true))` gets no stdlib and (post-tuple-rendering-fix) a hard "undefined @to_string" link error, for a reason invisible in the source. (2) **trigger overloading** — "do I get the standard library?" is silently answered by "did I write a `module` header?", two orthogonal concerns on one switch. Proposed direction: prelude implicit by default; satisfy the blank-slate need (minimal repros, `tests/stdlib/test_ir_*.spr` codegen fixtures, redefinition examples) via name **shadowing** (local def shadows prelude) or an explicit `no_prelude` pragma. Touches module semantics, shadowing rules, every importless test fixture, and compile-time/IR-size tradeoffs (force-bundling the prelude into every tiny fixture is non-trivial cost) — a design-doc-level change, NOT to be folded into parity work. Related: the `print` redesign item above; `project_importless_selfcontained_loudfail`.
- [x] `P1` Typed-codegen FLIP BLOCKER #2 — typed-built compiler stack-overflowed self-compiling the compiler. RESOLVED 2026-06-27 (branch `fix/typed-codegen-tco`). The initial "SIGSEGVs almost immediately → module-loader/bundler miscompile" hypothesis was WRONG: the new stack-overflow panic (commit 8ce597c) named the real culprit — `stdlib.compiler.lexer.tokenize_from` recursing one native frame per source token (early crash + low RSS is consistent with overflow, since lexing is the first phase). ROOT CAUSE: typed codegen did no TCO (`ast_to_ir` emitted every call as `IRCall`). FIX in three parts across the branch: (1) TCO IR ops + lowering + rooting (`IRTcoEntry`/`IRTcoLoad`/`IRTcoBack`); (2) the `tco_rewrite` post-translation producer (transitive returned-phi tracing); (3) the final gap — `tokenize_from` had a `tco_loop` but still left 2 native self-calls at **single-arm tuple-destructure matches** (`match scan_ident_next/scan_int_next(...) with | (next, tok) -> tokenize_from(...)`). A single-arm match lowers to one real arm + an `unreachable` exhaustiveness sibling → a single-incoming pass-through join phi; the old `tco_safe_hits` punted on it (removing the lone incoming empties the phi). FIX: removed `tco_safe_hits` (always keep hits) and replaced the one-pass dead-block drop with an iterative `tco_prune_dead_blocks` that strips a dropped block's `[_, D]` incoming from its `IRBr` successor's phis (parent-phi fixup cascade); empty phi ⟺ unreachable block, and the ret-feeding phi keeps its base-case incoming so the cascade terminates. Also handles both-branches-recurse joins for free. Regression: `test_tco_rewrite.spr` `make_single_arm_fn` (RED→GREEN). **`just flip-readiness` is now GREEN — all 4 steps incl. the typed-built compiler self-compiling the compiler to verifiable IR.** Note v1 scope: self-recursion + `i64` return only; mutual TCO and `Bool`/tuple returns are the P2 follow-ups below. The flip itself (rerouting `--emit-ir`/default through typed codegen) is the remaining step — see `docs/archive/p11-flip-handoff-2026-06-27.md` "Once self-compile is clean".
- [x] `P1` Typed-codegen flip blocker #1 — typed entry-point omitted `sprout_set_argv` → self-compiled compiler saw empty `argv`. FIXED 2026-06-27 (branch `fix/typed-entrypoint-argv`). The typed path emitted valid IR for the WHOLE compiler (469k lines, `opt --passes=verify` clean, links), but the resulting binary's `argv_all()` returned `[]` for every invocation, so the compiler's `main()` (`match argv_all() with ["--emit-ir", root | _] | …`) always fell through to the usage error. ROOT CAUSE: `ir_lowering.main_shim` emitted `define i32 @main(i32 %argc, ptr %argv)` that called `@__sprout_user_main()` but never called `@sprout_set_argv(i32 %argc, ptr %argv)` — so `g_sprout_argc/g_sprout_argv` stayed 0/NULL (`sprout_runtime.c:1382`). Direct codegen does emit it (`codegen.sprout:3093`, declared at `:3350`). FIX: `main_shim` now calls `@sprout_set_argv(argc, argv)` right after `entry:`, and `ir_header` declares `@sprout_set_argv(i32, ptr)`. Regression: `just argv-smoke` (`tests/argv_smoke/argv_echo.spr`) builds a tiny argv-matching program via `--use-ir-codegen` and runs it WITH args — verified RED (`got 'empty'`) pre-fix, GREEN post-fix. **KEY LESSON — parity is necessary but NOT sufficient for the flip:** `ir_runtime_parity.sh` runs every corpus binary with NO command-line args (`</dev/null`), so `argv_all()` was never exercised across all 109 files; the compiler is the first program that reads its own argv. This corrects the `docs/archive/p11-campaign-handoff-2026-06-24.md` framing of the flip as "mechanical": it is gated on typed-codegen SELF-COMPILE correctness (a strictly larger bar than parity) — see blocker #2 above, surfaced immediately behind this one.
- [ ] `P2` Lift the i64-only TCO restriction — TCO `Bool`- and tuple-returning self-tail-recursive functions too. BOTH codegen paths currently only tail-call-optimize functions whose body returns `i64`: direct codegen gates on `body_ret_is_i64` (`codegen.sprout:1663`, excludes `TConst "Bool"` and `TTuple _`), and the typed-codegen TCO work (`fix/typed-codegen-tco`, blocker #2) deliberately matches that restriction for v1 to stay safe. Consequence: a deeply self-tail-recursive function returning `Bool` (e.g. an `all`/`any`-style predicate loop) or a tuple (e.g. a scanner returning `(result, pos)`) builds one native frame per call and overflows the stack at depth — now *diagnosable* via the stack-overflow panic, but still a real overflow. Lifting it means the alloca-slot loop (and direct's `musttail` path) must handle non-`i64` return/slot types: `Bool` lowers to `i1`, a tuple to a struct, so the loop's `ret` type, the back-edge value coercions, and (for mutual TCO) the `musttail` signature match all need to accept those shapes. Scope: extend the return-type predicate to admit `i1` and small structs, add the back-edge store coercions, and add deep-recursion regressions for a `Bool`-returning and a tuple-returning self-recursive function under BOTH `--emit-ir` and `--use-ir-codegen`. Raised 2026-06-27 during the blocker-#2 (typed TCO) design.
- [ ] `P3` Phase B mutual-TCO: a member that is BOTH self-tail-recursive AND in a heterogeneous mutual-tail cycle keeps its **mutual** edge as a plain call. `mutual_tco_rewrite_fn` skips any fn carrying an `IRTcoEntry` (already self-TCO'd), so after `phase_b_unify` generates that member's `$u` variant, its mutual-cycle edge is not `musttail`'d. **No miscompile** — the self edge still self-TCOs; only the mutual edge builds a native frame per iteration (the pre-Phase-B behavior for that edge). A fix would interleave self-TCO and mutual-TCO on the same `$u` body (emit the loop head once, route both the self back-edge and the mutual `musttail` through it). Surfaced 2026-07-21 during the Phase B landing (branch `feat/mutual-tco-phase-b`, PR #225); design note in `docs/mutual-tco-phase-b-v0.md` §5a.
- [ ] `P3` Phase B / Tier-2 CPR: the trampoline-worker repack for ADT-returning cycle members (`emit_repack_one`, `ast_to_ir.sprout`) emits `IRRetUnboxed2` only — **width-2** (`{tag, val}`). A match-routed cycle member returning a **≥2-field-ctor** ADT would drop fields (repack returns `{tag, 0}`). **Currently unreachable, so latent — NOT a live bug:** CPR-worker routing (`is_simple_width2_arm`, gated on `max-ctor-arity ≤ 1`) never routes a wider ADT to a worker, so `emit_repack_one` never sees an arity-≥2 ctor. Reaching it (routing width-3 ADTs through a worker) would need `emit_repack_one` extended to build `{tag, f0, f1}` and the worker return type widened to `{i64,i64,i64}` sret — cf. the width-3 sret ABI in `docs/cpr-nested-product-unboxing-plan-2026-06-29.md`. Surfaced 2026-07-21 during the Phase B ADT-return landing (PR #225); design note in `docs/mutual-tco-phase-b-v0.md` §5b.
- [ ] `P3` **Phase B code-review follow-ups (2026-07-21).** Recall-biased review of the mutual-TCO arc; findings #1 (scope-blind name matching → silent miscompile) and #4-conjunct (structural `llvm_ret_type` gate) FIXED in the review change. Full disposition in `docs/mutual-tco-phase-b-v0.md` §12. Deferred, latent-or-cleanup:
  - **#2 arity re-check** — `pb_retarget_tail` zero-pads args to the SCC max arity without re-checking `list_length(args) == arity(name)`; Phase A does this at the IR site (`mutual_tag_hits`, `list_length(args) == g_arity`). Latent: a tail-position partial application would become a full application. Gated today only because `pb_ret_unifiable` rejects `TFunc` returns. Add the arity conjunct to the retarget.
  - **#3 param-type gate** — `mutual_filter_targets` (`ast_to_ir.sprout`) gates a `musttail` edge on arity equality only; `docs/mutual-tco-v0.md` §4a specified a per-edge `params_match` all-i64 check ("mandatory") that was dropped. Sound today because `lower_params` emits `i64` unconditionally; a future non-i64 param ABI (e.g. unboxed `double`) would emit a prototype-mismatched `musttail` (loud LLVM-verifier failure, not silent). Restore the check or assert the i64-uniformity dependency at the gate.
  - **#4-tuple (missed opt)** — broaden `pb_ret_unifiable` to admit `TTuple` (a boxed-i64 handle, musttail-safe) so tuple-returning heterogeneous-arity tail cycles are optimized instead of silently exhausting a green task's GC-root pool. **Blocker:** a tuple return has no `adt_index` entry, so if such a member is CPR-worker-routed the repack derives an empty ctor list — handle that path (or prove tuples never route) before broadening.
  - **#5 cross-module SCC names** — `pb_tail_graph`/`pb_fn_order` match bare `TVar` callee names against bare decl names at the pre-lowering seam; if qualified/mangled names ever appear on one side, a genuine cross-module heterogeneous tail cycle silently escapes Phase B (missed opt, not miscompile). Confirm the namespace coincides for bundled compiles; add a cross-module hetero-cycle test.
  - **#6 dual tail-grammar walk** — `pb_retarget_tail`/`_arms`/`_do` re-encode the exact tail-position grammar that `pb_tail_callees_sc`/`_arms`/`_do_last` already walk (the comment says "Mirrors … exactly"). Drift makes detection and rewrite disagree → an SCC unified but an edge mis-retargeted. Collapse to one tail-position walk parameterized by collect-vs-rewrite.
  - **#7 O(n²) SCC** — `pb_scc_of` computes each SCC by `mutual_reaches(f,g) && mutual_reaches(g,f)` per node pair (a full DFS each), and `mutual_filter_targets` redoes a DFS per call-graph edge; both run unconditionally in `compile_program_streaming` incl. the self-hosting bootstrap. Replace with one linear SCC pass (Tarjan/Kosaraju) shared by both phases, or gate behind a cheap "any tail self/mutual edge exists?" pre-filter. Also `mutual_reaches_loop` enqueues neighbors without a before-push seen-check.
  - **#8 dead copy** — `pb_scc_of_rest` is provably equal to `pb_scc_of` (names in `order` are unique); delete it and have `pb_scc_of` self-recurse.
  - **#9 test dilution** — `tests/stdlib/test_ir_codegen_closures.spr` T19 was weakened (arc-b) from an exact derived-name match to a bare `$raw` substring; restore an exact-name assertion for the collision-avoidance case.
  - **#10 vacuous eligibility map** — `build_ret_i64` computes `llvm_ret_type(ret) == "i64"`, but `llvm_ret_type` ≡ `"i64"`, so the "both members return i64" gate is a no-op and the only real backstop is the `ret_ty == "i64"` string test in `mutual_tco_rewrite_fn`. Make the map meaningful or delete it and document the single string test as the intentional gate.
- [ ] `P3` Populate `IRGetTupleField`'s `IRType` kind for `VarPattern`-bound scalar tuple fields (threading follow-up). The op is now **kind-aware** (branch `opt/ir-tuplefield-kind`): `IRGetTupleField` carries an `IRType`, `ir_rooting.op_produces_simple_heap` honours it (`IRTScalar` → no root), and the literal-pattern construction sites (`IntPattern`/`BoolPattern` in `bind_tuple_items`) emit `IRTScalar`. Regression: `tests/stdlib/test_ir_rooting.spr` T14 (scalar tuple field = 1 push vs T9's heap = 2). **Remaining:** the common case — a scalar field bound by `VarPattern` (e.g. `match coord with (x, y) -> x + y`) — still emits `IRTUnknown` because `bind_tuple_items` has no element-type info; covering it needs threading the scrutinee tuple's `List types.Type` from `translate_match` → `translate_match_arm_tuple` → `bind_tuple_items`. **Deprioritized to P3:** this is type-reclassification (same class as the Char-scalar flip, which measured ~0.2% root reduction — see `project_char_scalar_not_overrooting_win`), NOT the per-trigger re-rooting multiplier. The real memory win is the large-list-literal item below. Do this only if a measured hot path shows scalar-tuple-field over-rooting.
- [x] `P2` Large list-literal codegen → O(N²) GC rooting. **FIXED 2026-06-30 via root-once stack coalescing** (`stdlib/compiler/ir_rooting.sprout`; design `docs/archive/root-once-rooting-design-2026-06-30.md`). NOTE the original prescription below was WRONG: a list literal's elements are consumed exactly once, so there are zero reachability-redundant roots — a "stored-into-a-cell is reachable via that cell" analysis has nothing to bite on (verified against real IR). The cost was per-trigger re-rooting (O(uses)): a value live across K triggers was pushed/popped K times. The fix threads a persistent root stack across each block, pushing each value once and popping dead values from the top only (safe over-retention when lifetimes interleave; under-rooting structurally impossible by a superset argument). Measured: a 120-element literal dropped from 2943 MB / 8494 root ops to 40 MB / 890 (O(N²)→O(N), peak RSS ~N³→flat). Gates: test-stress 0 UAF, parity 111/111. *(Original framing, kept for the record: "teach `ir_rooting` that a value stored into a freshly-allocated cell is kept reachable VIA that cell's root" — does not apply; the lever was the rooting strategy, not reachability.)*
  - [x] **Follow-up DONE (2026-06-30):** merged `compiler_intrinsic_sigs_1..5` / `append_sigs` back into one literal (`stdlib/compiler/codegen.sprout`); reseed converges at 437 MB (bounded — coalescing makes the single 46-entry literal safe). `checker.sprout` `analysis_entries()` was already a single literal (never chunked), so needed no change. The interim lint was not added — un-chunked tables compile bounded under root-once, so it would guard a regression the rooting fix already prevents; revisit only if a future table regresses peak RSS.
- [x] `P2` Phase D B2 — leaf-loop GC-root elision. **LANDED 2026-07-11** (branch `phase-d-b2-root-elision`; `stdlib/compiler/ir_rooting.sprout`). `op_triggers_gc` peeks the `IRCall` callee name (`is_nonallocating_read`) and returns `false` for a verified allow-list of non-allocating leaf externs — `vector_get_direct`/`vector_mutset`/`vector_length` (allocating `vector_get` stays a trigger). Recognizer 3.03s→1.67s (~1.8×), accuracy 139/150; A*/nqueens flat = no regression. Seed reconverged (global change, not byte-identical). Tests T17–T19 in `test_ir_rooting.spr`; validated `just test-stress`. Docs: `docs/phase-d-numeric-fastpath-design-2026-07-11.md §B2`, `bench/results-2026-07-11-b2.md`. Follow-ups below.
  - [x] `P2` **B2 reach extension → non-allocating `IRCallUnboxed2` reads. LANDED 2026-07-11** (branch `phase-d-b2-unboxed-reach`, PR #163). `op_triggers_gc` peeks the `IRCallUnboxed2` callee (`is_nonallocating_unboxed_read`): non-trigger for the **8** verified non-allocating unboxed reads (`vector_get`/`map_get`/`map_nth_key`/`map_nth_value`/`bytes_get`/`str_char_at`/`argv_get`/`env_get`_unboxed), trigger for the **2** that allocate. **Runtime-verified allocation map (correction):** the landmine set is only `regex_find_range_unboxed` (`@sprout_alloc_range_val`, runtime:4404) and `term_read_line_unboxed` (`register_cstr`, runtime:4368) — `env_get_unboxed` does NOT allocate (returns `getenv()` ptr, runtime:4342; earlier notes wrongly listed it). **Result** (`bench/results-2026-07-11-unboxed-reach.md` + witness `bench/unboxed_read/`): a tight `MutVec Int` sum loop that IS root-bound on the unboxed read runs **~1.3×** faster (~8.3→6.3 ns/read); A*/nqueens/recognizer flat (not root-bound on unboxed reads — A* dilutes it with per-iteration work). Removes the **worker-internal** root around `vector_get_unboxed`; the larger per-read root — around the `mutvec_get_worker` *call* (a Sprout `IRCall`) — remains → needs the Koka-style inference below. Tests T20–T22; `SPROUT_GC_STRESS=1` green; seed reconverged.
  - [ ] `P3` **Compiler-inferred non-allocation (Koka-style) instead of a hardcoded list** (Kuba, 2026-07-11 — the list in `is_nonallocating_read` is an unchecked assertion, and manual annotation is NOT what's wanted; the compiler should determine non-allocation itself). **Target direction: interprocedural non-allocation inference** for Sprout functions — a fixpoint over the call graph where a function is non-allocating iff every op and every callee is, à la Koka's effect-row inference (model allocation as an inferred effect; the useful signal is the rare *negative* `noalloc`, since almost everything allocates). `op_triggers_gc` then consults the inferred per-callee result for `IRCall`/`IRCallUnboxed2` to a Sprout fn, replacing the name list for Sprout callees. **Foundation it requires (do first, cheap, ships alone):** the irreducible **leaf C-extern facts** — the compiler never sees C bodies, so these must be declared+checked no matter what. Add a CI/DoD checker (mirroring `runtime/APPROVED_BUILTINS` gating) verifying each tagged extern's C body invokes no `sprout_make*`/`sprout_alloc*`/`sprout_gc_maybe_collect_threshold`/`SPROUT_GC_PUSH` (sound for leaf fns; precise invariant = "on every returning path, transitively no `sprout_gc_maybe_collect_threshold`"). The inference bottoms out on these. **ROI note:** inference's payoff on the hot wrappers waits on CPR/unboxing making them non-allocating — but that has ALREADY landed for `mutvec_get` (→ `vector_get_unboxed`), so the near-term reach win is actually the small `IRCallUnboxed2` allow-list item above, and full inference is the durable end-state that removes the hand-list entirely. **Rejected:** `@noalloc` source annotation on `extern fn` — still a manual list, just relocated; doesn't meet the "compiler figures it out" goal. Also rejected: return-type shape ("boxes ⇒ allocates" — unsound: `vector_set -> Vector a`, `str_concat -> String` allocate + return heap) and `!{IO}` (≠ allocation: `vector_get_direct` is `!{IO}` non-alloc, `vector_get` is pure but allocates). Relates to operand-protection follow-ups (5)/(6) under the P11-item-4 entry above (same "single source of truth for the alloc property + IR-layer linter" theme).
- [x] `P1` GC memory phase 1 — exact-size `SproutObj`. **DONE 2026-07-03** (branch `gc-header-rewrite`). ADT objects allocate `8 + arity*8` bytes instead of the fixed 80-byte struct; per-arity freelists (0..9) linked through the tag word (arity-0 objects have no `f0`); alloc arity recorded in `ManagedNode.aux_slots` so the free path never consults `find_ctor`; `SPROUT_GC_LINEAGE` corpses padded to 3 fields so the poison backtrace slots (`f1`/`f2`) always fit. Field offsets unchanged → no codegen change, no seed refresh. New profiler counters: `max_probe`, drain-phase hit/miss hop split. Measured (self-emit): peak footprint 420→322 MB (−23%), GC time −45% (locality), wall −21%; nqueens flat as expected (vector-heavy). Gates: full suite, `test-stress`, canary run, examples compile — all green. Results in `docs/gc-profile-findings-2026-07-03.md` addendum.
- [x] `P1` GC memory phase 2 — **DONE 2026-07-05** (branch `gc-phase2-regions`; M-A1 headers+OBJ flip, M-A2 CSTR+O(1) `str_byte_len`, merged M-B/C regions+deletion). Membership is a region binary-search + per-region slot-start bitmap (8 KiB per 1 MiB region — a bare bounds check would deref garbage headers for any mid-region integer); marking lives in the header color bit; sweep walks regions linearly, rebuilds class freelists every cycle and releases empty regions; `ManagedNode`, `g_heap_index` (32 MiB STOPGAP) and all hash machinery deleted. Measured (vs pre-phase-1): self-emit wall 35.8→12.2s (2.9×), GC 26.1→3.6s (7.3×), footprint 420→212 MB; nqueens RSS 39→4.8 MB (8.1×); membership hops → 0. Follow-ups filed below. Results in `docs/gc-profile-findings-2026-07-03.md` addendum 2.
- [ ] `P2` GC generational step — bump-region nursery semantics on top of the phase-2 regions: minor collections over young regions, write barrier in `ref_write` + `vector_mutset`, age/remembered bits already reserved in the header (confirmed 2026-08-09: header bits 9–13 are free — kind is 0–7, color is bit 8). **Unresolved premise, and it is the correctness crux:** this entry puts the barrier in `ref_write` **+** `vector_mutset`, while the older deprioritized draft under *GC & Runtime Performance* says the remembered set is *"populated only in `ref_write` (the sole mutation primitive)"*. Those contradict, and a generational collector is only sound if the barrier catches **every** old→young pointer write. Audit first: enumerate every runtime function that writes a pointer into an already-allocated object, and make the barrier's coverage a checkable invariant rather than a claim. **Also required first:** make the sweep's freelists generation-scoped (see the landed staging entry under *GC & Runtime Performance*) — a minor collection that still rebuilds the whole heap's freelist is not proportional to the young set. ~~Primary target: nqueens-class workloads (33k cycles × O(heap) sweep is the remaining GC cost there).~~ Design constraints recorded in `project_gc_endstate_decision` memory.
  - **MEASURED 2026-08-09 across seven workloads — two claims in this entry were wrong. See `docs/gc-generational-v0.md`.** Instrument: `SPROUT_GC_AGEPROF=1` (age in the reserved header bits 9–13, counters at the mark choke point, the sweep, and both mutation primitives), calibrated by `just gc-ageprof-check` against two workloads with known answers.
    - **"Primary target: nqueens-class workloads" is contradicted.** The nursery's ceiling — the share of marking spent on objects that already survived a cycle — is **97% for the compiler** but 39% (nqueens), 48% (http_log_middleware), **13% (real HTTP server)**. And those ratios are of an already-negligible volume: the compiler marks 32.3M objects, the HTTP server marks 11.8k, i.e. **~38 objects per collection** (nqueens 60, http_log_middleware 23). Marking is already free there.
    - **GC is not the bottleneck outside the compiler at all.** Raising the threshold 64× changes nqueens 2.60s → 2.65s and http_log_middleware 4.98s → 5.20s (no speedup, 20× the RSS), and **`SPROUT_GC_DISABLE=1` makes nqueens *slower*** — 2.50s → 2.72s, peak RSS 6 MB → 970 MB, because fresh pages cost more than collecting. So "GC is 59%" is a property of the self-hosted compiler (85% immortal AST/IR), not a general fact. Build the nursery as a compiler/self-hosting optimisation or not at all.
    - **The barrier-surface crux is RESOLVED, and this entry had it right:** `ref_write` + `vector_mutset`, with every `MutVec`/`MutMatrix` write routing through the latter (`mutvec_set` → `vector_mutset`; `mutmatrix_set` → `mutvec_set`; the fused `mutmatrix_row_sub_scaled_go` → `vector_mutset`). BACKLOG:1355's "only in `ref_write`" is wrong. **Amended 2026-08-15 — the surface is now THREE: `ref_write` + `vector_mutset` + `vector_push`.** Landing growable `MutVec` added a second writing extern to `stdlib/mutable.sprout` (`mutvec_push` → `vector_push`), which stores a pointer into an already-allocated `VectorVal` and therefore needs the barrier exactly as `vector_mutset` does; it carries the ageprof hook already. This is the concrete instance of why this entry demands the coverage be *a checkable invariant rather than a claim*: the previous wording justified closure with "`stdlib/mutable.sprout` declares no writing externs, so no bypass", and one commit adding one extern silently falsified it. Re-audit on every new mutation primitive until that invariant is mechanised. The scheduler's `chan_pending`/`result` stores are **not** barrier sites — they are rooted by address and every per-task root context is scanned regardless of generation. `vector_set`/`map_set`/`native_set_insert`/`Builder` are persistent.
    - **The barrier must be TYPED, not unconditional.** The compiler makes 15,746 mutation calls total (2,051 storing a pointer, 1,855 recordable) — trivial. `digit_recognizer` makes **10,584,110** mutation calls of which **zero** store a heap pointer (all unboxed `Double`). An unconditional barrier in the runtime primitive would fire 10.6M times and record nothing; emit it only for pointer-typed element stores, which the compiler knows statically.
    - **Non-moving caps the churn workloads regardless.** Their cost is proportional to garbage (~4096 slots walked to reclaim ~4073 dead), and an in-place nursery must still visit every dead young slot. Only an evacuating nursery makes cost proportional to survivors, and moving is blocked by the rooting design. This matches Immix (PLDI 2008) §5.3: sticky-mark-bit generational over a mark-sweep base "does not improve sufficiently over MS to justify its use"; it becomes "a serious proposition" only over a mark-region base.
    - **Remembered set must be per-domain-shaped from day one** — BACKLOG:1355 proposes one global fixed-size array, but tier-2 share-nothing multicore is the declared direction and both Erlang (per-process heaps) and OCaml 5 (per-domain minor arena) key the young generation per process/domain.
- [ ] `P3` Region allocator polish: ~~open-region index~~ (DONE in PR #136's review-fix commit: validated `g_open_region_hint`); remaining: consider address-mask region lookup (1 MiB-aligned regions) to replace the binary search if the profile shows it. Design rationale for the shipped phase-2 collector lives in `docs/gc-header-rewrite-handoff-2026-07-03.md` and the findings addendum.
- [ ] `P2` C exhaustiveness for heap-kind dispatch — make `SproutHeapKind` complete (fold in `SPROUT_GC_POISON`), switch on the enum type with no `default:` arms, add `-Werror=switch` to the runtime clang lines in the justfile; converts silent wrong-dispatch after a new heap kind into a build break. Recipe + limits in `docs/gc-phase2-retro-handoff-2026-07-05.md` §1.
- [ ] `P2` Language design pair: bit-packed record types (`packed type` with named bit-fields, composing with `wrap`) + sized unsigned ints and bitwise operators. Motivated by the GC header word (~14 consumers; the phase-2 code review clustered there); the bitwise/sized-int gap also gates the deferred integer-tagging option and any runtime-in-Sprout work. Evidence + proposed shape in `docs/gc-phase2-retro-handoff-2026-07-05.md` §2. **The bitwise half is split out and LANDED 2026-08-17 (`docs/bitwise-int-ops-v0.md`)** — seven `Int`-monomorphic intrinsics in `stdlib.bits` (`bit_and/or/xor/not/shl/shr/shr_zf`), named functions rather than operators because `>>`/`<<` are function composition (F# and OCaml hit the same collision and did the same); six new IR ops, each one LLVM instruction, with the shift-count guard constant-folded for literal counts. **Still open here:** sized unsigned ints (`U8`…`U64`) and `packed type`, which need a sub-word representation decision the bitwise half did not. Note what the bitwise half now unblocks and what it does not: a pure-Sprout SHA-256 core is expressible today (`bytes_get` + `read_u32_be` already existed), while the GC header remains out of reach from Sprout for reasons unrelated to bit twiddling.
- [x] `P3` **Hex and binary integer literals (`0x`, `0b`). LANDED 2026-08-17**, in the same change as `stdlib.bits` — a bitwise API whose masks are unwritable in hex undercuts its own expressibility. Two notes worth keeping. It was **not** lexer-only as this entry predicted: the token deliberately carries the literal's *source spelling* and the parser decodes the radix (`int_from_lexed`), because decoding in the lexer would be shorter but would make `just fmt` reprint `0xFF` as `255` — a formatter that silently rewrites masks. And the prelude's `parse_int` was deliberately left alone: it is the public `String -> Maybe Int` for *user input*, where quietly accepting `"0xFF"` would change what existing validation code means. Tests: `tests/stdlib/test_int_literals.spr` (including hex in match patterns).
  - [ ] `P3` **`_` digit separator (`1_000_000`) — still open, and deliberately excluded above.** `_` is an identifier-start character, so `1_000` lexes as the int `1` followed by the identifier `_000`; changing that is a lexer decision of its own, not a follow-on from radix literals.
- [ ] `P3` **Reject a user declaration that shadows an intercepted compiler intrinsic.** Today a headerless file (no `module` line) that defines `fn to_double`, `fn print`, or `fn double_to_bits` has its call sites **silently lowered to the intrinsic** — the user's body is ignored, with no error at any stage. Verified mechanism, three links: extern names are never qualified (spec §"Externs are outside the module system"; `bundler.qualify_decl:1070`); `ast_to_ir.translate_call:4842-4853` matches the bare `fname` *before* consulting params/`let_names`/top level; and `bundler.qualified_name:157-161` returns the bare name when the module name is empty, which `scan_source_info:268-283` leaves empty for any file without a `module` line. Repro shape: one file, no `module` line, `fn to_double(n: Int) -> Double = 1.0`, one call. Fix direction: a declaration-time diagnostic naming the intrinsic. Pre-existing and class-wide; `docs/bitwise-int-ops-v0.md` §8.1 records it because adding seven more intercepted names widens the surface.
  - **A second, sharper form of the same bug: the intercept is inconsistent between captures and parameters.** `translate_call` consults `captures` **before** the intrinsic names but `param_known` **after** them (verified at `ast_to_ir.sprout:5023` vs `:5052`). So a *captured* variable named `bit_and` correctly wins and is applied as a closure, while a **parameter** of the same name is silently intercepted and its call lowered to the instruction — the same source-level name resolving two different ways depending on whether it was captured or passed. `fn apply(bit_and: Int -> Int -> Int) = bit_and(1, 2)` is the shape. Whatever diagnostic fixes the shadowing case should make this pair consistent rather than only covering top-level declarations.
- [x] `P2` Effectful iteration combinators — **LANDED 2026-07-09**, plan in `docs/archive/effectful-iteration-combinators-plan-2026-07-09.md`. Grid shipped in `stdlib/prelude.sprout` (`range_each`/`range_fold`/`list_each`/`list_fold`, effect-polymorphic, data-last; `range_fold` reordered to data-last); `examples/digit_recognizer/recognizer.sprout` rewritten against them (byte-identical training output, 329→301 lines); README "Effectful list iteration: Not Yet Supported" note replaced with an Iteration Combinators section; data-last convention documented in `docs/style-guide-v0.md` §10. **Deliberately NOT swept (2026-07-09):** the `stdlib/` internal helpers (`vec_map`/`vec_filter`/`vec_fold`/`dict_*_from`) — they are bundled into the compiler's self-compile hot path, so routing them through `range_fold` adds a per-element closure + `Maybe` unwrap (the recognizer measured ~5% from exactly this) for invisible internal-readability gain; and `stdlib/compiler/` has zero legal candidates (its loops thread `Result`/`Maybe` and short-circuit on first error, which a linear fold cannot express). Optional tiny follow-up if ever wanted: `pow_loop` (math.sprout, non-hot) and the three `dict_*_from` helpers are clean linear folds. **Original design intent below.** Add a generic `{range,list} × {each,fold}` grid to `stdlib/prelude.sprout`: `range_each`/`list_each` (imperative, no accumulator) and effectful `range_fold`/`list_fold`, all effect-**polymorphic** (`f: … -> … !{e}`) so pure callers are unaffected. Removes the dominant verbosity in effectful-over-`MutVec` code — the hand-rolled `if i >= n then base else do …; recurse(i+1)` counter loop (motivating consumer: the ≈18 loops in `examples/digit_recognizer/recognizer.sprout`; also the GC safety-net `churn(n, acc)` helpers, `docs/gc-phase2-retro-handoff-2026-07-05.md` §3). **Verified 2026-07-09**: the effect system already supports this (recursive `!{IO}` and `!{e}` HOFs compile+run) — it is a stdlib gap, not a language gap; the stale README "Effectful list iteration: Not Yet Supported" note gets removed. Data-last arg order (matches `map`/`fold`); reorders the lone-outlier `range_fold` to data-last (2 real callers + golden-IR regen). Also **documents the prelude's data-last argument convention in the style guide** (satisfies the original §4 task here). Generic-over-`Foldable` unification is a separate follow-up gated on B2 below (recursive instance-method dispatch).
- [ ] `P3` GC hardening follow-ups from the phase-2 review (confirmed observations, deliberately not fixed in PR #136):
  - **HDRCHECK region-walk validation**: under `SPROUT_GC_HDRCHECK=1`, walk each region asserting every set slotmap bit has a valid header kind and that strides land exactly on the next set bit / bump — catches any header-word corruption within one GC cycle (today only CSTR length and OBJ arity are checked; 8 of 10 kinds have no layout check).
  - **`is_large` uniformization**: large objects special-case ~9 branch sites (alloc, lookup, sweep passes, release); evaluate treating them as 1-slot regions with a minimal slotmap so the walks unify. Do it before the generational step multiplies the branch count.
  - **"Keep ≥ 1 normal region" invariant single ownership**: currently enforced by both pass 2's `kept_normal > 1` guard and a post-pass `open_new_region()` fallback; pick one owner and make the other an assert.
  - **OBJ tag-write window**: `sprout_alloc_obj_raw` writes aux=arity, the real `(tag<<4)|arity` lands one call later — safe today (no GC point between), but passing the tag into the alloc path would delete the window a future maintainer could widen.
- [ ] `P3` Measure `str_eq`'s length fast-reject now that membership is a region binary-search (not a hash): two lookups guard every strcmp including equal-length and static-literal cases; profile whether a short-string threshold (skip lookups under ~16 bytes) or identifier interning is the better shape. `freelist_hits`/`region_count` profiler fields landed in PR #136 make this measurable.
- [ ] `P3` Sweep pass fusion: pass 3 (freelist rebuild) re-walks every region calling `slot_bytes` per live slot; it could fuse into pass 1 if freelist entries from pass-2-released regions are handled (tombstone check or region-generation counter). Only worth it if gc-profile shows sweep dominated by the extra pass.
- [ ] `P3` Static string literals: emit a length-prefix header word in the data segment so ALL strings (not just heap ones) get O(1) length via a uniform header read. Codegen change → seed refresh; deliberately out of phase 2's runtime-only scope.
- [ ] `P3` Embedded-NUL string semantics: `String` today silently truncates at the first interior NUL byte (e.g. binary-ish `proc_run` output). Decide at spec level: keep "no interior NULs" but enforce loudly at ingestion boundaries, or move to length-delimited semantics. Touches spec-v0 §strings; independent of the phase-2 runtime work.
- [ ] `P1` `wrap` value rendered as garbage / crash via user `ToString` + `${x}` interpolation. Surfaced 2026-07-02 while adding daemon logging. Repro (`/tmp/repl_repro.txt`): `wrap Minutes = Int` + `instance ToString Minutes { fn to_string(x) = ` + "`${x} minutes`" + ` }` + `let m = Minutes(10)`, then eval `m`. Expected `10 minutes`; the eval path prints `\xNN\xNN minutes` — the wrapped `Int` value `10` is rendered as if it were a heap `String` pointer (deref of `10` as `char*` → garbage bytes). Same fault crashes the interactive REPL daemon (`analysis service: empty response`) when the corrupt pointer is unmapped; non-interactively it merely garbles output. Reproduces deterministically via `SPROUT_ANALYSIS_SERVICE_LOG=/tmp/x ./build/sproutd < /tmp/repl_repro.txt`. Likely a representation/dispatch fault in the compiler core (`ast_to_ir`/`codegen` `to_string`/interpolation lowering for `wrap`-typed args) rather than a rooting bug — `${x}` where `x : Minutes` should dispatch through the user `ToString Minutes` instance but appears to route the underlying-`Int` value into the string-rendering path. Shared-core bug: affects any consumer of the compiler (REPL, analysis daemon, LSP). NEXT: minimize (does plain `to_string(Minutes(10))` without interpolation also corrupt? does a non-`wrap` newtype-via-`type` differ?), then IR-diff the `to_string`/template lowering for the wrapped arg.
- [x] `P1` Forwarded type-variable constraint resolved to a parametric instance instead of forwarding the dict (dispatch soundness). **FIXED 2026-07-06** (surfaced 2026-07-05 while landing W8). Repro: `vec_sort(foldable_to_vec([5, 3, 1, 4, 2]))` SIGSEGV'd. `vec_sort(vec: Vec a) -> Vec a where Ord a = vec_sort_by(\value -> value, vec)`: the call needs `Ord k` with `k = a`, which should **forward `vec_sort`'s `Ord a` parameter**. Instead the resolver lowered a fresh closure statically calling `@__tc_Ord_Vec_a_compare`, so sorting a `Vec Int` by identity compared the `Int` keys via `Ord Vec` → `vec_length` dereferenced the integer as a pointer → crash. **Root cause:** in `infer.sprout`'s `resolve_obligation`, the callee's constraint variable (`k`) is recorded in the `@constrained_N` marker by *source* name, but `prog_to_fresh`/the callee scheme use *generalized* names, so `dict_get("k", prog_to_fresh)` always missed and forced the `Nothing` branch. There, after `scan_prog_to_fresh_for_instance` found no concrete instance (the variable resolved to a type variable), resolution fell straight to `resolve_one_constraint_tdict`'s first-concrete-arg heuristic, which grabbed the `vec: Vec a` argument → `Ord (Vec a)`. **Fix:** inserted a `scan_fwd_markers` forward scan *between* `scan_prog_to_fresh_for_instance` and `resolve_one_constraint_tdict` — a constraint on a forwardable type variable now forwards the in-scope dict before ever reaching the first-concrete-arg heuristic. Regression test `tests/stdlib/test_vec_sort_forwarding.spr`. **Follow-up:** the deeper source-name-vs-generalized-name marker mismatch (which would buy multi-same-class-constraint precision, cf. the `Eq a, Eq b` limitation) is untouched — the forward scan uses the first-marker heuristic, consistent with the `Just`-branch path, correct for single-constraint callees like `vec_sort_by`.
- [ ] `P2` **B1 — inline multi-line `do`-block lambda as a call argument is a parse error.** Surfaced 2026-07-09 during the effectful-iteration brainstorm. `f(0, 4, \i -> do { _ <- g(i); h(i) })` fails with `Parse error: Expected )`; a single-expression lambda (`\i -> g(i)`) parses fine, and a named top-level step function works. Consequence: iteration-combinator call sites (see §5 effectful-iteration item) must lift any multi-statement step into a *named* function, which is the difference between a good and a maximal verbosity reduction for `recognizer.sprout`-style code. Lifting this is the highest-leverage ergonomic follow-up on top of the combinators. Scope: the expression parser must accept a `do` block as a lambda body inside argument position (likely a precedence/terminator issue where `do` in an argument doesn't know where to stop). Interacts with the open currying/n-ary decision only loosely.
- [x] `P1` **`vec_sort_by` projection sort (key type ≠ element type) crashes — mis-forwarded `Ord` dict. FIXED 2026-07-13.** Surfaced while landing the bottom-up `vec_sort` stack-safety rewrite. `vec_sort_by(f, vec)` where `f: a -> k` with `k ≠ a` (e.g. sorting `Vec (Int,Int)` by an `Int` field, or `Vec IntRange` by `range_start`) SIGSEGV'd via a self-referential `Ord`-dict (`merge_runs → __tc_Ord_Tuple_a_b_compare` on Int keys → deref). **Was pre-existing and algorithm-independent** (the old top-down merge sort crashed identically); the **uncovered sibling of the #141 `scan_fwd_markers` fix** — its own follow-up flagged the untouched "source-name-vs-generalized-name marker mismatch," which this closes. **Root cause:** the `@constrained_N:name` marker keyed the constraint var by its SOURCE name (`k`), but `typecheck_decl` overwrites the callee's env scheme with `generalize(...)`, whose quantified vars are the fresh instantiation names — renamed once more by the body's unification `s2`. At an external call site the resolver instantiates that generalized scheme, so `prog_to_fresh` is keyed by the post-`s2` names; the source-name marker missed the precise `dict_get(prog_var, prog_to_fresh)` lookup in `resolve_obligation` and fell to the order-dependent `scan_prog_to_fresh_for_instance` heuristic, which grabbed the ELEMENT type `a` (`Ord (Int,Int)`) instead of the key type `k` (`Ord Int`). **Fix** (`infer.sprout` `canonicalize_constrained_markers`, called from `check_fn_body`): rewrite each marker's var to its canonical post-`s2` name `apply_subst(s2, prog_to_fresh[source_var])` — by construction the generalized scheme's ftv for that constraint — so the call-site `dict_get` hits the precise (already-tested) Just branch. Non-regressive: a miss falls through to prior behavior. Regression test `tests/stdlib/test_vec_sort_projection.spr` (element ordering + stability, the latter deferred by the stack-safety commit and now runnable). The masked `examples/aoc_2025_day_5.sprout:83` (`vec_sort_by(range_start, ranges)` on `Vec IntRange`) now runs correctly on multi-range input.
- [ ] `P2` **B2 — recursive instance-method dispatch fails instance resolution.** Surfaced 2026-07-09. A class method that calls *itself* recursively from within its own instance body fails to resolve the dictionary: `No instance of <Class> for List in instance method <m>`. Verified the pure control fails identically, so this is **not** effect-related — it is the pre-existing tyvar-identity / dict-resolution gap (cf. `project_typevar_identity_generalization_gap` memory) manifesting for self-recursive instance methods. Today's `Foldable List.fold_values` sidesteps it by delegating to a standalone `list_fold_go` helper. **Blocks** unifying the new effectful iteration combinators under a single generic `Foldable` (effect-polymorphic) class method; until fixed, the combinators stay as concrete `range_*`/`list_*` standalone functions. Fixing this unblocks a `class Each f { each(f: a -> Unit !{e}, xs: f a) }`-style generalization.
- [ ] `P3` **B3 — `;` statement separator in a `do` block lexes as an error.** Surfaced 2026-07-09. `do print(h) ; print_all(t)` fails with `Lex error: Unexpected character ;` — so the README's own "Effectful list iteration" workaround example (which uses `;`) does not actually parse. Either implement `;` as an intra-`do` sequencing separator (newline-equivalent) or fix the README to use newline-separated `do` statements. Low urgency; the newline form works today. (The README note itself is being removed by the effectful-iteration work; ensure no other doc still shows `;`.)
- [ ] `P3` **Single traversal for the alloc-summary pre-pass vs the streaming emit.** Surfaced 2026-07-11 (code review of the alloc-fixpoint PR #164). `ir_pipeline.summarize_decls`/`summarize_workers`/`summarize_lifted_then_fn` hand-duplicate the structure of `stream_decls`/`stream_workers`/`emit_lifted_then_fn` (same `translate_decl_with_idx`/`emit_worker_for`/`synthesize_init_globals_fn` calls, same Nothing/Just-triple unwrap, same lifted-then-fn order); only the per-fn leaf action differs (accumulate `fn_raw_summary` vs emit). Two orchestrations kept in lock-step by hand → a future translation/worker-emission change applied to only one path silently desyncs the summary from the emit pass. **Degrades SAFELY** (missing summary entry → conservative over-rooting, per `ir_rooting`'s safety note), so this is a drift/maintenance concern, NOT a soundness bug. Deeper fix: one higher-order traversal (a visitor/generator yielding `IRFunction`s) that both the pre-pass and the emit pass consume with different per-fn actions. Medium effort; the double-translation CPU cost is a separate, measured, accepted tradeoff (`bench/results-2026-07-11-alloc-fixpoint.md`). Also noted in review: the new call-op classifiers (`op_raw_callee`/`op_noncall_trigger`/`op_triggers_gc_summary`) use `_` catch-alls for the call arms, unlike `op_triggers_gc`'s exhaustive no-`_` discipline — a new IRCall-variant would silently drop from the call graph (conservative, not a UAF). Consider making the call-family classification exhaustive too if a third call op is ever added.
- [ ] `P2` **Scalar-replacement follow-ups** (tuple-CPR + intra-function SRA LANDED 2026-07-25; design + status in `docs/scalar-replacement-v0.md` Appendix B). Separable extensions:
  - **Rivers-demo `bake_tile` residue — CLOSED 2026-07-25.** The second per-tile allocation was **not** a `Maybe` box (already CPR-unboxed) but the `from_ordinal` typeclass-dispatch **dictionary**; removed by concrete-instance devirtualization (`docs/devirtualization-v0.md`). `bake_tile` is now **allocation-free**. (The earlier "Stage 2 — arg-position `Maybe`" framing of this item was based on a mis-attributed residue and is retired.)
  - **Devirt beyond the clean case — DONE 2026-07-25** (`docs/devirtualization-v0.md`, increment 2). Superclass-expanded dispatch (`Ord` → sibling super block dropped, 2 closures→0) and context-constrained instances (`Eq (Maybe a) where Eq a`, `Ord (Maybe a)` → resolved inner dicts forwarded as trailing args). Dispatch block identified by method presence in `ctx_inst[key]`. Only remaining devirt gap: recursively devirt'ing a concrete *inner* dict (monomorphization — deliberately out of scope).
  - **Stage 2 — arg-position / nested CPR — ASSESSED LOW-PAYOFF 2026-07-25, not planned.** Census of `stdlib/compiler/*.sprout` found the compiler's heavy tuple use is overwhelmingly *match-scrutinee* position (already unboxed by Stage 1 / intra-fn SRA). Genuine arg-position tuple opportunities across the whole compiler collapse to **~2 call sites** — the `(Bool, Set)` accumulator threaded through `scan_op_raw`/`scan_ops_raw`/`scan_blocks_raw` in `ir_rooting.sprout:1102-1117` — and even that is blocked by heap-field-tuple support (`Set` field) + needs *nested* (forwarding-through) CPR. `Maybe`/`Result` arg-position is rarer still (matched immediately, or pre-existing AST fields). The original motivator (`biome_rgb`/`tile_kind_of`) needed the accessor inliner, not this. Revisit only if heap-field tuples land and profiling flags `ir_rooting` scan.
  - **SRA beyond do-blocks.** The current SRA is do-block-localized (fires on `let x=E; …; match x with (tuple)` inside a `do`). Extend to pure `let..in`, and across a Maybe/Result do-bind in the continuation (today `sra_rest_plain` denies those — the continuation runs in `translate_do_bind_*`, which reset the SRA map; thread the map through those helpers instead of resetting).
  - **Heap-field tuples.** `(String, String)` / `(a, List a)` producers: `IRCallUnboxed{2,3}` slots holding heap values must be rooted at the call site, and `op_heap_def` (single `Maybe`) must report multiple slots. `scalar_tuple_width` currently excludes non-scalar fields (all-scalar milestone), so these stay boxed.
- [ ] `P3` **Named `RuntimeLet` record for the runtime-let representation.** Surfaced 2026-07-11 (Kuba). `classify_let_decls_ir` returns `List (source.GlobalName, typed_ast.TypedExpr)` — an anonymous positional pair. Now that the name is a `wrap GlobalName` (landed 2026-07-11 with the alloc-fixpoint work; `source.sprout`), the remaining cleanup is to name the pair as a record `RuntimeLet { name: source.GlobalName, body: typed_ast.TypedExpr }` for self-documentation at its construction (`ast_to_ir.classify_let_decls_ir_acc`) and consumption (`synthesize_init_body_loop`, `ir_pipeline.build_alloc_summary`). **Blocked on records maturing** — record support is WIP (field access landed, cf. line ~207; `RecordExpr`/`GetFieldExpr` inference exists but the feature is not yet load-bearing in compiler code). Do once records are trusted in the self-hosted compiler; low urgency. Broader follow-on: adopt `GlobalName` at the other global-name sites (`IRGlobalDecl` name, `IRStoreGlobal`/`IRRegisterGlobalRoot` targets) so the wrap/unwrap seam shrinks — currently the name is unwrapped immediately for those raw-`String` IR ops.
- [ ] `P2` **Operators as signatured functions (B1) — design landed, impl deferred.** See `docs/operators-v0.md`. Primitive operators (`!`, unary `-`, arithmetic `+ - * /`) have **no type signature** — they are hand-coded in the typechecker (`infer_unary`, `check_arith`) and lowered to dedicated IR. This asymmetry with typeclass-backed operators (`==`/`++`, which *do* have signatures via class methods) is what let the `! 3 → 2` miscompile exist (unconstrained operand flowed to `xor i64, 1`). **The soundness hole is already closed** by a stopgap that tightens `infer_unary` to unify operands against the fixed expected type (`!` : `Bool`; `-` : `Int`/`Double`); this item is the *architecture* follow-on. **Recommended model (B1, OCaml-style):** declare operators as prelude `extern fn` with real signatures (so typing falls out of ordinary call resolution — the miscompile becomes structurally impossible), desugar `!e`/`a+b` to calls at parse time, and have `ast_to_ir` intercept the operator-function name to emit the primitive instruction directly (exactly like `recognize_string_builtin("str_concat")`). **Runtime cost: zero** — identical IR, no call, no inliner required; reuses machinery Sprout already ships. Also moves fixity/precedence to a declaration table (prerequisite for future user-declared operators). **Open sub-decision:** whether arithmetic becomes a `Num`/`Numeric` typeclass or stays monomorphic (v0 keeps it monomorphic). **Separate follow-on (B2):** a general function inliner + const-folder would let operators have real fallback bodies (GHC/Rust model) and is independently useful for the accessor inliner / heap-field-tuple levers.

- [x] `P2` **Double transcendental math + Int/Double module split. LANDED 2026-08-06.** Design of record: `docs/math-transcendental-v0.md`; measurements: `bench/results-2026-08-06-math-transcendental.md`. `stdlib.math` is now the `Double` layer (`fabs`→`abs`, `fclamp`→`clamp`, plus new `exp`, `ln`, `log2`, `log10`, `log(x, base)`, `cbrt`, `pow(Double, Double)`); the `Int` surface moved verbatim to `stdlib.math.int`. All pure Sprout — **no new builtin, `APPROVED_BUILTINS` untouched** — at ~1e-13 relative accuracy, with `pow` exact for integer exponents *whenever every intermediate product is exact*, which covers Stefan-Boltzmann `T⁴`. (This entry originally said "bit-exact for integer exponents" without qualification. That is the false claim retracted in the post-merge-review entry below and in `docs/math-transcendental-v0.md` §11.5 — corrected here too, since an unqualified version surviving in the backlog is how it would get requoted.) Unblocks Tsiolkovsky Δv (needed `ln`) and sigmoid/softmax (needed `exp`, which `docs/nn-gap-analysis.md` had wrongly claimed required a C extern — corrected there). Zero compiler change: `module_name_to_path` maps dots to slashes, and `stdlib/compiler.sprout` beside `stdlib/compiler/` is precedent. Follow-ups below.
  - [ ] `P2` **`Eq Double` / `Ord Double` — the decision `numeric-types-v1-draft.md` §7.1 said was "needed before `Double` ships", and `Double` shipped without it.** `Double` has exactly one instance in the whole stdlib (`ToString`, `prelude.sprout:814`). Live consequences today: `check_eq` does not type-check on `Double`, so `stdlib/test.sprout` carries `check_approx`/`check_approx2` as a workaround; and `stdlib.math` deliberately ships **no** `Double` `min`/`max`/`sign` because they would force the question. The blocker is real, not neglect — IEEE NaN is unordered, so any total `Ord Double` is a lie. Prior art diverges: **Rust** gives `f64` only `PartialOrd`, never `Ord`; **Haskell** defines `Ord Double` and it is a well-known footgun (`compare NaN x` misbehaves, breaking `maximum`/`sort`). The draft's §7.1 lists three options (inherit IEEE / add `PartialOrd` as a superclass split / wrap in `Maybe`). Decide before adding any `Double` comparison-based API.
  - [ ] `P3` **`Double` `min`/`max`/`sign`.** Blocked on the item above. Note C's `fmin`/`fmax` are *not* simply `if a < b` — they return the non-NaN operand when exactly one is NaN, which is a deliberate deviation from IEEE ordering that a naive implementation gets wrong.
  - [ ] `P3` **Remaining elementary functions**, in rough demand order: `sinh`/`cosh`/`tanh` (each one line over `exp`), `expm1`/`log1p` (the accuracy-preserving forms for arguments near 0, where `exp(x)-1` and `ln(1+x)` lose most of their significant digits to cancellation), `hypot` (overflow-safe `sqrt(x²+y²)`), `sigmoid`. None blocks anything today; add on first real caller so the API is shaped by use. **`asin`/`acos` DONE 2026-08-11** — and the "over `atan`+`sqrt`" one-liner this entry predicted was not quite the shape. Two corrections worth carrying to the functions still listed here. (1) The textbook `atan(x / sqrt(1 - x²))` is unusable over *this* `atan`: at `|x| = 1` it feeds `±inf` into `atan_reduce`, whose halving step evaluates `inf/inf` = NaN, so the most-used input returns NaN. The half-angle form `2·atan(x / (1 + sqrt(1 - x²)))` has a denominator bounded below by 1 and no such point. (2) **Accuracy near 1.6e-15 still left `acos(1.0)` NEGATIVE** (`-8.9e-16`, outside POSIX's `[0, pi]`), because `2·atan(1)` overshoots `pi/2` by one ulp — and `acos(dot(u, v))` on parallel unit vectors hits exactly `1.0`, so that is the common case. Small error does not imply in-range when the true value sits on a boundary; the fix is answering the endpoints directly, which is *complete* because `acos` leaves 1 like `sqrt(2·eps)` (one ulp inward the true value is already 1.5e-8). Expect the same class of endpoint check for `hypot` and `tanh`. Write-up: `docs/math-transcendental-v0.md` §15.
  - [ ] `P3` **Euclidean `mod` vs C `fmod` naming across the two modules.** `stdlib.math.int.mod` is Euclidean (result always in `[0, n)`). If a `Double` remainder is ever added to `stdlib.math`, the obvious name `mod` would collide *semantically* with the Int one unless it is also Euclidean — but C's `fmod` and every mainstream float remainder is *truncated* (sign follows the dividend). Pick deliberately: either implement a Euclidean `Double` `mod` so the name means one thing across both modules, or name the truncated one distinctly (`rem`/`fmod`) and document why the pair differs.
  - [x] `P3` **`cbrt` (and `sqrt`) speed. DONE 2026-08-06 — and this entry's diagnosis was wrong.** It attributed the remaining cost to "7 Newton passes each carrying an unavoidable `x / (guess * guess)`". Measured, the division was **not** the dominant cost: the per-pass *convergence check* was. Replacing `abs(next - guess) < 1e-15 * guess` with a fixed 6 passes gives **`sqrt` 2.97x** (8.16 → 2.75 ns, and **bit-identical** output at all 400k sampled points) and **`cbrt` 2.21x** (9.82 → 4.44 ns, ~1 ULP shift on 3.5% of inputs with a *better* worst-case residual, 1.005e-15 → 9.38e-16). The mechanism is branch misprediction, not arithmetic: the guard makes the trip count input-dependent so the loop-exit branch mispredicts nearly every call (~5 ns at 3.5 GHz), and a fixed count lets LLVM fully unroll (`fcmp` 2 → 0, conditional branches 8 → 4). Inlined `fdiv` count barely moved, 13 → 11. The division-free inverse-cube-root iteration was also measured and **rejected** — 5.41 ns vs 3.20 — because five serially-dependent multiplies are a longer latency chain than one `fdiv`. Write-up: `docs/math-transcendental-v0.md` §12; tests: `tests/stdlib/test_math_root_accuracy.spr`. Note the polynomial-initial-guess idea this entry suggested is now moot for both functions.
  - [x] `P2` **The `*_wide` gap is range reduction. CLOSED 2026-08-07** — option (b), Double bit access, shipped as the `double_to_bits`/`double_from_bits` intrinsics, and **all four reductions converted** (`ln`, then `sqrt`/`cbrt`/`exp`). No function in the layer is magnitude-dependent any more: `sqrt(1e300)` 25.1 → 5.9 ns, `sqrt(1e-300)` 27.0 → 5.8, `cbrt(1e300)` 20.7 → 8.0, `exp(688)` 21.5 → 9.3, `exp(-700)` 26.6 → 9.6, `ln(1e-300)` 22.5 → 3.7 — all **bit-identical** to the stride ladders, which are retained and exported (`sqrt_strided` etc.) purely as the oracles for `tests/stdlib/test_math_wide_reduction.spr`. Normal magnitudes unchanged to within noise (1.00-1.04x), so there is no trade. **Three things worth carrying forward.** (1) The roots need `floor(e/2)` and `floor(e/3)` while Sprout's `/` truncates toward zero; the difference bites every negative odd exponent (half of all inputs below 1.0, two in three for cbrt). Correcting after the fact needs a branch plus — there being no `%` — a multiply, and it cost `cbrt` 10% at normal magnitude (7.41 vs 6.66 ns), the same "gave back at the common case" trap `ln` hit. Biasing the numerator non-negative first makes truncation *be* floor: `floor(e/n) == (e + 1074)/n - 1074/n`, since a binary64 exponent never goes below -1074 and 1074 divides by both 2 and 3. Verified exhaustively over the domain. (2) `exp` needed the inverse direction — build `2^k` from an integer — which required the language's first `Double -> Int`, done with the magic-number trick (`bits(d + 2^52) - bits(2^52)`). Kept PRIVATE; a public one needs a rounding-mode and out-of-range decision (see the follow-up below). (3) A first `exp` version short-circuited `k > 1023` to inf, but `exp(709.78)` has `k = 1024` and a FINITE result (~1.79e308) — `test_math_transcendental` caught it while the new sweep did not, because its step stopped at 698 and never reached the overflow boundary. A sweep's bound is part of its assertion. Details: `docs/math-transcendental-v0.md` §13-14. Original analysis follows.
  - [ ] `P3` **No test-only visibility, so a test oracle must be `export`ed and ships in every consumer's IR.** Surfaced 2026-08-07 by code review of the O(1) reduction work. `stdlib/math.sprout` keeps four stride-ladder implementations (`sqrt_strided`, `cbrt_strided`, `exp_strided`, `ln_strided`) purely as accuracy oracles for `tests/stdlib/test_math_wide_reduction.spr` — they earned their keep immediately, being what revealed the `sqrt(NaN)` regression. But a test can only reach them if they are exported, and exported functions are emitted into the IR of any importing module **even under a selective import list**: `tests/smoke_shapes/10_double_math.spr` imports exactly `(sqrt, cbrt, exp, ln, pow)` and still carries 1136 lines of oracle IR, ~7% of the file. Measured blast radius today is that one file (no shipped example imports `stdlib.math`; `astar` imports `stdlib.math.int`), so the cost is currently near-nil, but it grows with every consumer. **Not fixable by moving the oracles into the test**: they need `sqrt_iter`/`cbrt_iter`/`exp_series`/`ln_series`, which are private, and duplicating those would make the oracle actively worse — a legitimate series change would then read as a reduction divergence, a false alarm in the one test meant to be trusted. Sharing the series and varying only the reduction is the point. **INVESTIGATED AND SHELVED 2026-08-08 — no decision taken, nothing implemented. Full write-up: `docs/test-visibility-v0.md`.** Prior art now surveyed and verified against primary sources (Java classpath + JPMS, Rust, Go); three findings worth not re-deriving. (1) **Visibility in Sprout is not a type-system property** — the parser discards `export` (`parser.sprout:1728` `skip_export`), the bundler recovers it by a *textual line scan* (`bundler.scan_source_info`), and enforcement is name-binding in `apply_one_import`, not a check. Consequence: `qualify_all_modules` qualifies **all** decls unfiltered by `exported`, so privates already land in every importer's IR — which is why co-locating tests in the library file is dead, not merely ugly. (2) **Across all four languages surveyed, exclusion is upstream of visibility** and the test-access grant never lives in the shipped artifact: Java uses one-directional classpaths (`test` scope is absent from the compile classpath) and, under JPMS, `--patch-module`/`--add-exports` at *invocation* time rather than the language's own `exports … to` friend mechanism; Rust uses `#[cfg(test)]`; Go uses the `_test.go` filename suffix. Sprout has no such build-mode file-selection stage at all — that, not the missing visibility tier, is the actual gap. (3) **The blocker for any file-based option** is `module_loader.resolve_module_path` (`:164`): `module_name_to_path` returns `Just` for *every* `stdlib.*` name, so `extra_roots` is never consulted for them and a second file for one module name is unreachable. Making it a root-ordered set needs the filesystem-existence check its own comment defers as "multi-root disambiguation needs IO" — and that same change is what would let a *library* build see the test half, so the exclusion guarantee becomes an invariant to gate ("the test root appears in `extra_roots` only for a test-entry compile"), not a structural fact. **Open when resumed:** Go's `export_test.go` shape (an *additive* file, module identity unchanged) was not known when the mirrored-tree/same-module-name option was preferred, and the two should be compared directly — see the doc §9-10. When this lands, drop the `export` on the four `*_strided` entry points.
- [x] `P3` **Expose a public `Double -> Int` conversion. CLOSED 2026-08-14** — shipped as `math.to_int : Double -> Maybe Int` + `to_int_or`, with `ceiling`/`truncate`/`round` completing the `Double -> Double` rounding family beside `floor`. Design and the answered questions: `docs/double-to-int-v0.md`; the closure is also recorded on the `P1` duplicate of this item in §Language/runtime. **Correction to the survey line below**, which is why the prior art is re-stated in the design doc rather than reused: Haskell's four `RealFrac` methods settle the *rounding-mode* question but **not** the out-of-range one, because they target unbounded `Integer` and never face it; Go explicitly declines to answer it; and Rust's saturation was a repair for undefined behaviour in a pre-existing *total* operator, not a fresh design choice — Rust std has no checked float→int conversion at all (no `TryFrom<f64> for i64`). The decision rested on Sprout's own `parse_int : String -> Maybe Int` instead. What actually made the question tractable was **separating the layers**: rounding stays in `Double` and is total, so only one function is ever partial. Original entry follows. `stdlib/math.sprout` now has a private `round_to_int`, added 2026-08-07 for `exp`'s scale factor (it rounds to nearest and converts in a single add — see the note at its definition), implemented with the magic-number trick over `double_to_bits`. It was deliberately not exported: a public conversion needs a **rounding-mode** decision (truncate toward zero, floor, round-half-even?) and a defined answer for non-integral inputs, values outside `Int` range, and NaN/inf — none of which the internal use needed. Prior art to survey when this is taken up: Rust's `as` (saturating since 1.45) vs `to_int_unchecked`; Go's truncating conversion with implementation-defined out-of-range behaviour; Haskell's `truncate`/`round`/`floor`/`ceiling` as four separate `RealFrac` methods, which is the most explicit option and fits Sprout's no-silent-lies stance. Note this is now the *only* remaining reason the "there is no `Double -> Int` conversion" line in `docs/math-transcendental-v0.md` §5 is still true at the language level. Design doc: `docs/double-bit-access-v0.md`. Two of the three options below rested on claims that were false when measured, and both corrections are worth keeping: **(a) is not free of libm** — `llvm.frexp.f64.i32`/`llvm.ldexp.f64.i32` do not lower to inline instructions on arm64, they lower to `bl _frexp`/`b _ldexp` and pull `_frexp`/`_ldexp` as undefined externals; the runtime pulls zero libm math symbols today and no link line passes `-lm`, so (a) would have ended "stdlib.math is libm-free" and, once libm is linked for `frexp`, the case against calling libm's `exp` collapses. **(b) does NOT unblock exact Double printing** as claimed below — that blocker is wide-integer arithmetic (128-bit/bignum for Ryū/Grisu), disproved directly by a pure-Sprout converter that round-tripped 5 of 7 samples; nor does it unblock `Eq`/`Ord Double`, which is gated on the NaN-ordering *decision*, not on capability. What made (b) the clear winner is a fact the entry below missed: under the i64-uniform ABI a `Double` and an `Int` are **already the same LLVM type**, so the bitcast is **zero instructions** — `fn id_d(x: Double)` and `fn id_i(x: Int)` emit byte-identical `define i64 @f(i64 %x) { ret i64 %x }`. It is therefore the *cheapest* option, not the heaviest: no runtime symbol, no `APPROVED_BUILTINS` entry, no dependency, implemented exactly like `to_double`. Result for `ln`, measured 2M-call warm sweeps averaged over 3 runs, results **bit-identical** to the stride ladder at every sample: `ln(1e-300)` 22.48 → 3.65 ns (6.2x), `ln(1e300)` 22.26 → 3.86 ns (5.8x), `ln(pi)` 4.51 → 4.04 ns, `ln(2.0)` 4.02 → 3.73 ns. The headline is not the peak speedup but that the O(1) form is **flat at 3.6-4.0 ns across the whole exponent range** where the ladder was magnitude-dependent. Follow-up work, in order: convert `sqrt_reduce` and `cbrt_reduce` (they need exponent/2 and exponent/3 handling, so parity and mod-3 must be preserved — more delicate than `ln`'s straight split); `stdlib/math.sprout` carries a NOTE at the old helper site recording the two traps found the hard way (bind the biased exponent once — recomputing it made normal-magnitude `ln` 5 → 7 ns; and fold a subnormal lift into the exponent *before* the `* ln2`, never as a separate subtraction after, or the single-rounding property is lost and `tests/stdlib/test_math_wide_reduction.spr` fails on 5 subnormal samples). Original analysis follows, including the two falsified claims, left visible on purpose. — This is the only remaining structural cost in the Double math layer, at **13–20x libm** (`exp` near x=688 21.7 ns, `ln(1e-300)` 20.9 ns, `sqrt(1e300)` 19.1 ns). libm extracts the binary exponent with a load, shift and mask: **O(1)**. Sprout cannot express that at all — verified: the only `Double`↔`Int` bridge in the language is `to_double` (`prelude.sprout`, a bare `sitofp` with no runtime symbol), and `double_to_string` is the *only* function in the entire runtime that reinterprets a Double's bits. So reduction is **O(binary exponent / stride)**: the coarse stride ladders cut it from ~1070 steps to ~20, and ~20 compare-multiply-branch steps against one mask is precisely the 13–20x. **Direct evidence it is the reduction and not the series:** making the Newton iteration ~3x faster (see the entry above) moved `sqrt(1e300)` from 19.0 to 19.1 ns — i.e. not at all — while normal-magnitude `sqrt` went 8.16 → 2.75 ns. **This needs a design decision before any implementation, per AGENTS.md "Design Change Process" (prior-art survey required) and "Builtin vs Stdlib" rules 4–6 (a builtin needs approval up front).** The options, in the order they should be considered: (a) **lower to LLVM intrinsics** (`llvm.frexp`/`llvm.ldexp`) — a *codegen* change, so `APPROVED_BUILTINS` stays untouched, and it is the escalation `stdlib/math.sprout`'s own header already names as the sanctioned one; (b) expose **bit-level Double access** as a language primitive (a `Double`↔`Int` bitcast), which is more general — it would also unblock exact `Double` printing, hex-float literals, and a `Double` `Ord`/`Eq` implementation — but is a real surface-area commitment; (c) a **C builtin** for `frexp`/`ldexp`, which rules 4–6 disfavour and which (a) makes unnecessary. **Do not start on cost grounds alone:** rule 6 requires a concrete measured application bottleneck, and there is none today — 20 ns at the exponent extremes is dwarfed by any surrounding allocation or I/O, and a Δv or emittance evaluation is one or two calls. The right trigger is a real workload, or wanting (b) for one of its *other* consequences.
  - [x] `P2` **Golden IR corpus had zero coverage of the Double math layer. CLOSED 2026-08-06.** Found by the change above: rewriting *both* Newton iterations moved **0 of 57** goldens. No corpus member imported the Double `stdlib.math` — the only `stdlib.math` reference across all 57 was `examples/astar.sprout`, which imports `stdlib.math.int`. So `just ir-golden-diff`, the change-detector wired into CI in PR #26, was structurally blind to `sqrt`/`cbrt`/`exp`/`ln`/`pow`. `tests/smoke_shapes/10_double_math.spr` closes it (the corpus builds from `examples/*.sprout` + `tests/smoke_shapes/*.spr`); the corpus is now 58 files and the gate was confirmed to **fire** — 1 difference on a single pass-count change — not merely to pass. General lesson for the corpus: it covers what the examples happen to use, so a module with no example is invisible to it.
  - [x] `P3` **`pow` with a fractional exponent (~40 ns) is NOT a defect. CLOSED 2026-08-06.** It costs more than `exp` + `ln` measured separately (~14 ns) because the two are *serially dependent* — `exp` cannot start until `ln` finishes — so the row pays full latency, whereas the standalone `exp`/`ln` rows have independent argument streams and pipeline across iterations. libm shows the same effect at the same shape (5.9 ns for `pow` against 1.1 ns for `exp`), and both languages are measured identically, so the ratio is honest and there is nothing to fix. Recorded so it is not repeatedly re-investigated as a slow row.
  - [x] `P1` **Post-merge review fixes. LANDED 2026-08-06** (follow-up PR to the above). A high-effort adversarial review after merge found 10 confirmed defects, all fixed; full write-up in `docs/math-transcendental-v0.md` §11. Two were silent wrong answers: **`sqrt` returned grossly wrong results above ~1e35** (pre-existing — Newton seeded with `x` itself only halves per step, so the 60-iteration cap was hit before convergence; `sqrt(1e40)` gave 8.674e21 instead of 1e20, and `linalg`'s vec3 length was ~130 orders out at astronomical scale), fixed with an exact power-of-four range reduction; and **`pow` lost the sign of an underflowing odd negative power** (`0.0 - v` yields +0.0 — negation of zero needs unary `-`, which lowers to `fneg`). Two C99 gaps: `pow(-inf, non-integer)` returned NaN where F.9.4.4 confines that rule to *finite* negative bases, and `abs(-0.0)`/`floor(-0.0)` had inverted signed-zero behaviour because `-0.0 < 0.0` is false. A ~110x per-call cost cliff (reductions were linear in the binary exponent; `exp` near x=688 cost 1099ns against the 9.2ns published) fixed with coarse stride ladders + wide-exponent benchmark rows so the region is sampled from now on. `ln` made **exact** at powers of two by counting reduction steps instead of accumulating `ln2` per step, which also improved `pow`'s fractional accuracy 250x. And three false documentation claims corrected, the worst being "`pow` is bit-exact for integer exponents" — repeated in the normative spec — which held only because the test happened to pick an exactly-representable case.
  - [x] `P2` **Golden IR corpus was gated by nothing. FIXED 2026-08-06.** `scripts/ir_golden_diff.sh` was invoked by no `just` target and no CI workflow, which is how two stale snapshots (`examples__astar`, `examples__repl_hosted`) survived a green `just test` *and* `gate-quick` — and why the commit before them was itself a stale-golden cleanup (`ec13b90`). Now wired as `just ir-golden-diff` (~55s, 57 files) into both `gate` and `ci-fast-gates`, so CI blocks a stale golden; deliberately **not** in `gate-quick`, which exists for the seconds-scale loop. `just ir-golden-snapshot` is the refresh path. The audit that found this also found the same defect class in `scripts/ir_byte_identical_check.sh` and in `gate-audit` itself (a drift guard CI never ran), so the durable fix is a new **assertion B in `gate-audit`**: every `scripts/*.sh` must be reachable from the justfile or a `.claude` hook, or be allowlisted with a written reason — and `gate-audit` now runs in `ci-fast-gates`. This also closes the `P3` "Golden-IR corpus diff for codegen changes" item under *CI / Build Performance*, whose corpus and scripts had been built but never wired or marked done — which is the root cause of the rot, not an accident.
  - [x] `P2` **`where` bindings took their type from the body instead of the right-hand side. FIXED 2026-08-06.** Reported as "`where` tuple destructuring misinfers `Double` as `Int`"; the reproduction showed the report was **narrower than the defect in two ways and wrong in one**. Not tuple-specific: scalar `where a = x * 2.0` failed identically. Not call-specific: `where a = 2.5` — a literal right-hand side, no call — failed too. And the claim that "a single-name `where` binding of a `Double` is fine" was **false**; those cases only appeared to work when the body independently pinned the type (`floor`'s `r > x` compares against a `Double` parameter, so `r` was forced), which is why the fault looked intermittent. Root cause: `where a = v` desugared to an immediately-applied lambda with an *unannotated* parameter, `(\a -> body)(v)`. `infer_call_general` infers the callee before the arguments, so the body was typed while `a` was an unconstrained variable, `check_arith` defaulted it to `Int`, and only then did the call unify `Int` against `v`'s real type — information flowed body → binding, i.e. backwards. Fix: `where` now desugars to the **same node `let … in` already used**, a single-arm `match` on the value (`build_let_binding_match`), and a match infers its scrutinee first. `let` never had the bug, which is exactly why two desugarings for one construct was the underlying flaw. Retired 168 lines of parser: the lambda form needed a synthetic `__sprout_where_N` parameter for non-variable patterns and hence a capture-avoidance scan, while a match arm binds the pattern directly. ~75 of those lines (the `expr_names` family) were **already dead** before this change, reachable only from a `fresh_where_tmp_name_from` that had no callers — invisible to unused-function detection because mutual recursion gives every member a caller. `spec-v0.md` §5.1 and typing rules 13–14 now specify the inference direction normatively. **Correction to an earlier claim in this entry:** the `ln_reduce`/`sqrt_reduce`/`cbrt_reduce` accumulator threading in `stdlib/math.sprout` was described here as a workaround for this bug that could now be rewritten to return `(mantissa, exponent)`. That was measured on 2026-08-06 and is wrong as a recommendation — the tuple form is **2.8x slower** for `ln` (12.4 vs 4.3 ns/call, bit-identical results), because tuple-return CPR does not fire on the self-recursive edge and so boxes once per reduction step. The bug did make the tuple form fail to compile at the time, which is how it came to be recorded as the reason; the shape is now kept on its own merits and documented as such in `stdlib/math.sprout`. The CPR gap is filed separately under *Sprout-IR / Model-C Codegen*.

#### Type-system review findings (2026-08-13)

> Multi-lens adversarial review of the type system (design + implementation), each finding
> verified by an independent agent that had to reproduce it against `compile_driver_bin_stage1`,
> then re-reproduced from scratch by the reviewing agent. Report: `docs/type-system-review-2026-08-13.md`.
> Two more findings from the same review are filed under *Dispatch Soundness & Diagnostics*
> (compound-head constraint dispatch; ambiguous class tyvar). The unifying root pattern across
> six of the seven: **a site that converts "I do not know yet" into "anything you like"** rather
> than into a deferred obligation or a located error.

- [x] `P1` **Declared type variables are not rigid: a const-free resolution passes the W3 guard
  (`infer.sprout:5536-5555`). FIXED 2026-08-13** — `rigidity_violation` now rejects a written
  variable that resolves to ANY compound type (not just one mentioning a `TConst`) and rejects
  two written variables resolving to the same variable, reporting the latter as "type variable b
  merged with declared variable a". `type_has_const`/`tuple_has_const` are gone; the resolution
  is rendered back through the programmer's own names (`fresh_to_written`/`render_written`), so
  the compound message reads "forced to b -> c" instead of leaking "$t41". The `_unann` skip is
  untouched. Blast-radius survey before landing: the compiler self-hosts through the new check
  (stage-3), all 51 examples compile, full suite green. Fixtures
  `tests/conformance/type_error/rigid_signature_{var_merge,compound}.spr` (both RED-verified
  accepted-then-SIGSEGV before the fix) plus the positive guard
  `tests/stdlib/test_rigid_signature_accepts.spr`, which pins the shapes that must keep
  compiling — including the two `_unann` forms the skip protects. Original analysis follows.
  `instantiate_with_vars` (`unifier.sprout:310`) mints signature
  tyvars as ordinary flexible metavariables — no skolemization — so `unify_applied`'s TVar/TVar
  arm (`unifier.sprout:227-231`) merges two of them freely. The only guard is the post-hoc
  `rigidity_violation`, which rejects a declared var **only** when its resolution contains a
  `TConst` (`type_has_const`, `:5559`). Every const-free resolution therefore passes:
  `fn snd_as_first(x: a, y: b) -> a = y` is accepted and its scheme silently narrows to
  `forall a. a -> a -> a`; so is `fn weird(x: a, g: b -> c) -> a = g`, where `a` is forced to a
  compound arrow. The code comment at `:5548-5552` states the current intent explicitly ("a
  variable bound to a purely-variable structure … is still fully polymorphic — not a violation"),
  which is true for a *structure* but false for a *merge*: `a = b` is a constraint the signature
  did not declare. **Miscompile window:** same-file, caller textually above callee, so the caller
  commits against `pre_scan`'s un-narrowed scheme. Callee-first rejects cleanly; cross-module
  rejects cleanly (the bundler emits the imported module first). Reproduced end-to-end — the
  IR calls `str_len` on a raw `i64` 7 and SIGSEGVs (exit 139), and the bogus value is also
  pushed as a **GC root**, so the type lie reaches the collector. In the orderings that *are*
  rejected, the error blames the caller rather than the declaration whose body never satisfied
  its own signature. **Fix:** in `rigidity_violation`, (a) reject any resolution that is not a
  bare `TVar`, and (b) reject pairwise duplicates among the declared vars' resolutions. Keep the
  `_unann` skip at `:5540` — it is correct and load-bearing (see the item below). The principled
  alternative is real skolemization in `instantiate_with_vars`, machinery that already exists in
  `instantiate_ctor_pattern` (`unifier.sprout:325`), but W3 deliberately avoided it to protect
  `@fwd` dict forwarding, so that route reopens the dict-forwarding question.
- [ ] `P1` **`_unann` placeholders are QUANTIFIED into the scheme published for forward
  references (`infer.sprout:193`, `:255`, `:301`, `:306`).** `scheme_from_fn_parts` (`:169`)
  synthesizes `_unann` for an omitted return type and `_unann_<param>` for an omitted parameter
  type, and `collect_ret_type_vars`/`collect_param_type_vars` put both into the scheme's
  **quantified var list**. `pre_scan_fn_decls` → `register_fn_decl_scheme` (`:5875-5890`)
  registers that scheme in the global env, so a call checked before the callee's body
  instantiates the placeholder to a fresh var and unifies it with whatever the caller wants.
  Nothing ever reconciles the caller's committed choice against the callee's real inferred type
  — the compiler prints the correct type in its own `--phase check` listing while emitting IR
  that contradicts it. `fn report(xs: List Int) -> String = "count=" ++ summarize(xs)` above
  `fn summarize(xs: List Int) = list_length(xs)` compiles clean and SIGSEGVs in `str_concat`.
  Confirmed genuinely quantified (not merely a shared unconstrained mono var) by instantiating
  the same forward callee's return at two different types in one caller expression. The
  unannotated-**param** form has the identical hole, which matters because this repo's own
  `compiler.sprout` cache params are unannotated for bootstrap compatibility (see the M7 item).
  **Do not "fix" this by removing the `_unann` skip in `rigidity_violation` (`:5540`)** — that
  skip is correct and necessary (removing it rejects every unannotated-return function, e.g.
  `rcompose`; see `docs/fundamentals-code-review-handoff-2026-07-03.md:239-242`), and it fires
  inside the *callee's* body check, which in the failing order runs after the caller has already
  committed. **Fix:** keep minting the `_unann` TVars for the type shape but leave them OUT of
  the `Scheme` binder list, so a forward caller sees a monomorphic unknown; pair with a located
  diagnostic at the forward call site, since a mono var cannot be shared across declarations in
  a `GlobalEnv` with no global substitution. Principled version: infer declarations in SCC
  dependency order, falling back to annotation-required only for genuine mutual recursion
  through an unannotated signature.
  **Correction 2026-08-13 to the "Fix" above — it does not work as written.** "Leave them out of
  the binder list" assumes the placeholders are per-declaration, and they are not: the return
  placeholder is `types.TVar(types.tyvar_id("_unann"))` (`:306`) — one fixed string shared by
  EVERY function with an omitted return type, and `"_unann_" ++ name` (`:301`) shared by every
  omitted parameter of the same name. Quantification is currently the only thing keeping them
  apart, because instantiation freshens them per use. Unquantified, all of them would collide in
  a single substitution and the first declaration to constrain `_unann` would constrain the rest.
  So the mono route needs **per-declaration unique placeholder names** as a prerequisite —
  e.g. `_unann@<qualified fn name>`. With that in place the placeholder becomes a genuine shared
  unknown between the forward call site and the callee's later body inference, which is the
  deferred-obligation shape that would make the forward reference CORRECT rather than merely
  rejected: the caller's use and the callee's inferred type meet in one variable, and a conflict
  surfaces as an ordinary type error at the callee. The cost is a monomorphism restriction on
  forward references — a forward-declared unannotated function could no longer be used at two
  different types in one expression — which is a user-visible acceptance change and needs a
  decision, not a unilateral pick. (This is roughly what SCC-ordered inference buys within an
  SCC anyway, at much lower cost.)
- [x] `P1` **An existential skolem escapes into a top-level scheme through an unannotated return
  (`unifier.sprout:390-410`). FIXED 2026-08-13** — `typecheck_decl` now scans a `FnDecl`'s
  inferred type with the existing `types.type_mentions_skolem` and rejects at the declaration;
  `finish_let_decl` does the same for a top-level `let`, which was a **second, untracked route**
  found while fixing this one (the value restriction's ftv test reads a skolem as "fully
  resolved, nothing ambiguous" because a `TConst` has no free variables, so
  `let leaked = match Boxed("hi") with | Boxed x -> x` bound monomorphically at the hidden type).
  Diagnostics are a new "escapes its scope in `<name>`" pair; the advice is deliberately not
  "annotate the return type", since writing one only moves the error to the rigidity scan.
  `spec-v0.md` §5.6 gained the missing clause — its escape rule only said the hidden type may not
  be "used at a concrete type", which describes the annotated case and left the inferred one
  unstated. Fixtures `existential_escape_{unannotated,top_let}` and `existential_merge_indirect`
  (all three RED-verified: accepted, and the merge one printed `2`, i.e. the merge
  `existential_merge.spr` exists to reject went straight through); `test_existential_0a.spr`
  gained the unannotated consumer and re-box shapes as the over-rejection guard. All 8 existential
  suites pass, compiler self-hosts (stage-3), 51/51 examples. Original analysis follows.
  A skolem is a rigid `TConst` (`$sk<n>`), so it has no free type
  variables; `generalize_resolved` computes `list_diff(ftv(resolved), env_ftv)` and never inspects
  the resolved type for skolems, so it rides through the decl's generalization at
  `infer.sprout:4962` as a fixed constant. Because a `TConst` is not quantified, instantiation
  never refreshes it — **every call site of the decl shares one rigid type**.
  `fn unbox(b: Boxed) = match b with | Boxed x -> x` gets the scheme
  `main.Boxed -> $sk2130`; the program prints a raw heap address for a String and accepts a
  heterogeneous `[Int, String]` list as homogeneous. **This defeats the fixture the repo already
  maintains for exactly this property** — `tests/conformance/type_error/existential_merge.spr`
  rejects the direct merge, but routing the identical shape through this one-line helper is
  accepted. `types.type_mentions_skolem` exists and has exactly **one** call site in the whole
  compiler (`unifier.sprout:217`, to reword a mismatch message); nothing checks a generalized
  scheme. The annotated form (`-> a`) is caught by the rigidity scan, which is why only the
  `_unann` path leaks. Blast radius is bounded: class dispatch on the escaped skolem is guarded
  (`resolve_skolem_given`, `infer.sprout:1223`) and coercion to a concrete type is rejected; the
  leak reaches runtime through fully-polymorphic externs such as `print`. **This falsifies the
  premise recorded in `docs/gadts-v0.md:342-343`** — "A leaked skolem is an inert, unusable type,
  which is why nominal rigidity is sound without levels." It is not inert once it reaches a
  top-level scheme. **Fix:** call the existing `types.type_mentions_skolem` at the
  decl-generalization site and reject with the existing located "existential type escapes its
  scope" diagnostic — one predicate call at one site. Add a `merge_indirect` fixture (unpack via
  an unannotated one-line helper) beside `existential_merge.spr` so the indirection cannot
  silently defeat it again. Update `gadts-v0.md` §342-343 either way.
- [x] `P1` **Field access on a not-yet-resolved receiver mints a fresh unconstrained tyvar
  (`infer.sprout:4080-4083`). FIXED 2026-08-13** — implemented as the queue the entry's own "Fix"
  paragraph describes, not the cheap backstop. `unifier.InferState` gains a fourth `Ref` holding
  parked `FieldObligation receiver_type field_name result_var pos` records; the `| _ ->` arm
  parks one instead of walking away from the fresh variable; `infer.discharge_field_obligations`
  settles them in `typecheck_expr`, the declaration boundary where the substitution is complete
  and before `apply_subst_typed_expr` runs. Rounds repeat while any obligation settles, because
  one discharge can unblock the next (`let i = o.inner in i.n`); when a round settles nothing the
  remaining receivers are undetermined for good and the FIRST in source order is reported.
  Obligations are taken-and-cleared on the error path too, so a failed declaration cannot leak
  them into the next one's environment.
  **CORRECTION 2026-08-13 (independent post-merge review of the landed commits).** The first
  landing was wrong in two ways, both reproduced. (i) **The soundness hole was not closed.**
  `discharge_against_record` dropped the obligation whenever the field lookup missed — receiver
  resolved to a non-record, or to a record without that field — leaving the result variable FREE
  for generalization to quantify. `fn coerce_via_nonrecord(p) = let s = p.x in if p == 0 then s
  else s` typed as `forall a. Int -> a`: precisely the coercion this item exists to remove. Only
  `ast_to_ir` caught it, at lowering, so `--phase check`, the LSP and sproutd all accepted the
  unsound scheme. The comment justifying the drop ("stealing it here would only change which
  message the user sees") was simply wrong — it is not a message difference, it is a free
  variable. A miss is now `OneFailed` carrying the existing "Unknown record type or field: X.y"
  text. (ii) **It over-rejected.** Obligations were discharged inside `typecheck_expr`, i.e.
  BEFORE the return-type unification and before `apply_let_annotation`, so a receiver determined
  only by the signature was reported as undetermined: `fn f(p) -> P = if p.ready then p else p`
  was rejected — contradicting the spec sentence this same change added, and advising the user to
  annotate the parameter, the one annotation that does not help. Discharge moved out of
  `typecheck_expr` into `finish_field_obligations`, which each of the three declaration-level
  callers invokes after its own unification; `typecheck_expr` still clears on the error path so a
  failed body cannot leak obligations. Fixtures
  `tests/conformance/type_error/field_access_{no_such_field,nonrecord_receiver}.spr` plus a fifth
  check in `test_field_access_deferred.spr`.
  Message: ``Cannot infer the record type of `.x` — nothing in this declaration determines what
  the value being read is. Annotate the parameter or binding``; a resolved-but-conflicting access
  gets ``Record field type mismatch reading `.x`: …``. Beyond closing the hole this fixes a
  latent incompleteness: chained access through an unannotated receiver (`o.inner` then `.n`)
  previously died in `ast_to_ir` with the position-less "field access '.n' on a non-record value"
  and now compiles. Blast radius measured, not assumed: a 413-file `--phase check` sweep of
  `examples/`, `tests/stdlib/` and `stdlib/` produced a byte-identical failure set before and
  after, the compiler self-hosts through the new check (stage-3), 51/51 examples compile and
  `ir-golden-diff` reports 58 files / 0 differences. Fixtures
  `tests/conformance/type_error/field_access_{unknown_receiver,deferred_mismatch}.spr` (both
  RED-verified: accepted, one SIGSEGV in `str_len`, one typed `forall a b. a -> b`) plus the
  positive guard `tests/stdlib/test_field_access_deferred.spr`, which pins that a field read
  BEFORE the call that resolves its receiver must keep working — the case a naive
  "reject unresolved receivers" fix would have broken. `docs/spec-v0.md` §5's record section now
  states the rule. Original analysis follows. `get_field_from_resolved`'s `| _ ->` arm invents a fresh tyvar
  for the field's type when the receiver has not yet resolved to a `TConst`/`TApp`, and emits
  `TGetField(…, TVar fresh, pos)`. **No deferred obligation is recorded and nothing revisits the
  node**, while `ast_to_ir` goes ahead and lowers a real offset-resolved field load
  (`sprout_field(%p, 0)`). Later unification is then free to pin that variable to any type.
  `fn bad(p) = let s = p.x in str_len(s) + zero(p)` checks clean as `main.P -> Int` and SIGSEGVs;
  `fn coerce(p) = p.x` gets the scheme `forall a b. a -> b`. **Order-dependent within a single
  expression**, which makes it a non-principality demonstration as well as a soundness hole:
  `zero(p) + str_len(p.x)` is correctly rejected while the semantically identical
  `str_len(p.x) + zero(p)` compiles and crashes. Annotating the parameter always closes it.
  The maintainers have already patched one manifestation of this exact fallback — the comment at
  `infer.sprout:2263-2265` names it as the cause of a lambda-argument bug, fixed with the
  two-pass `infer_call_args`; the general unannotated-parameter case remains open. Distinct from
  the tracked `assert_resolved_typed_expr` item under *Dispatch Soundness* and from the
  unknown-field sibling at `:4112-4115` (which `ast_to_ir` does catch): those cost a bad error
  message, this one costs memory safety. **Fix:** record a pending
  `(receiver_type, field_name, result_var, pos)` obligation on `InferState` and discharge the
  queue at the end of `check_fn_body` under the final substitution `s2`, erroring with a position
  if the receiver is still unresolved or lacks the field — mirroring the existing
  constrained-marker post-`s2` fixup. Cheap defence in depth: extend `assert_resolved_typed_expr`
  (`:5420`) to inspect the `TGetField` type slot it currently discards — **but note that pass is
  still dead code** (only self-recursive call sites), so this backstop only exists once the
  "wire in the dead `assert_resolved_typed_expr` pass" item lands.
- [ ] `P2` **Numeric defaulting fires before a deferred field obligation is discharged, so
  `Double` fields under arithmetic get a spurious `Int vs Double` error
  (`infer.check_arith`, `infer.sprout:2782`).** Found by the post-merge review of the
  deferred-field-obligation change; reproduced. `check_arith` unifies its two operands with each
  other and then tries `Int` **first**. Two deferred field accesses are both still free at that
  moment, so both default to `Int`; discharge then unifies `Int` against the real field type and
  fails: `fn total(a, b) = (a.price * b.price, tag(a) + tag(b))` on `type Item = (price: Double)`
  errors ``Record field type mismatch reading `.price`: Type mismatch: Int vs Double``. Valid code,
  rejected. Note this is an INCOMPLETE FIX, not a regression: before the obligation queue existed
  the same shape was a silent Int-arithmetic miscompile on a Double payload, so the loud error is
  strictly safer than what it replaced — but it is still wrong. Moving the discharge later does
  not help; the defaulting happens during body inference, long before any declaration-level
  unification. **Fix:** make numeric defaulting yield to an undischarged field obligation — either
  by having `check_arith` skip the `Int`-first attempt when an operand's type is a variable that a
  parked obligation will bind, or by giving the obligation queue a chance to run before defaulting
  rather than only after. Needs a look at how defaulting interacts with the queue generally, since
  the same ordering hazard applies to any other "try a concrete type first" site.
- [ ] `P3` **`head_name_matches` suffix-matches the final dotted segment, so a compound-head
  constraint can bind to another module's same-named type (`infer.sprout:1917`).** From the same
  review. `where Sh (Box a)` stores the SOURCE token `#app:Box`, while resolved argument types
  carry module-qualified names, so the match is `str_ends_with_suffix("other_module.Box", ".Box")`.
  Two modules each defining `Box` are indistinguishable and the scan takes whichever argument comes
  first — the exact class of guess the `#app:` change was made to eliminate, just narrowed from
  "any concrete argument" to "any argument whose type's last segment matches". **Fix:** qualify the
  head at canonicalization time (resolve the constraint's `TypeExpr` through the same
  name-resolution the parameter types get) so the comparison can be exact.
- [ ] `P3` **`iface_codec` head-token vocabulary gained `#app:<Name>` with no iface version bump
  (`iface_codec.sprout:48`, `:533`).** From the same review. `decode_iface_version` still accepts
  only `"6"` and `compile_driver.sprout:191` still emits `IfaceFile(6, …)`, although the precedent
  recorded in that same comment block is that v3→v4 was bumped precisely for a head-token format
  change. `--check-iface` therefore reports `OK:` on a v6 file that an older v6-speaking compiler
  would decode as a concrete-ctor head literally named `#app:Box`. No live miscompile today —
  `module_loader.sprout` does not consume ifaces yet — so this is a forward-compatibility/contract
  gap rather than a bug. **Fix:** bump to v7 on both sides, and add a `#app:` case to
  `tests/stdlib/compiler/test_scheme_roundtrip.spr` (`:157`), which has none.
- [x] `P2` **A single-constructor type cannot be destructured in a `do` bind — rejected in every
  spelling (`parser.sprout:492-503`, `:529`, `:544`). FIXED 2026-08-13** — done as the "Fix"
  paragraph below describes. A no-`else` do step whose pattern the parser cannot lower directly
  now becomes `DoStepTotal` and desugars through `build_do_total`, the same staircase as the
  `else` form carrying ONE branch, so W5 decides refutability against the pattern's TYPE — where
  spec §5.2.1 already put it. The syntactic predicate survives only to pick the direct-lowering
  fast path (`ast_to_ir.do_bind_captures`); it no longer decides legality. `wrap` and
  single-ctor ADT destructuring now work in `<-` and in do-`let`
  (`tests/stdlib/test_do_bind_single_ctor.spr`, 4 checks, RED-verified as the parse error first).
  Genuinely refutable patterns are still rejected and get a strictly better diagnostic:
  "Non-exhaustive match on main.Shape — no branch matches Square" instead of the blanket
  "refutable `<-` binding in a do block requires an `else`", which named nothing.
  Fixture migration: `tests/conformance/parse_error/refutable_do_bind_no_else.{spr,err}` is
  **deleted** and superseded by `tests/conformance/type_error/do_bind_refutable_{ctor,let}.spr` —
  the property it pinned still holds, but the diagnostic legitimately moved phase, and the old
  file could not have caught a regression anyway: its scrutinee was an undefined name, so it
  never got past the parse error. The new pair covers `<-` and do-`let` with programs that
  actually compile up to the exhaustiveness check. A tuple pattern in a do-`let`
  (`let (a, b) = e`) also errored before and now works, via the same path. Spec §5.2.2 gained
  the irrefutable-no-`else` case; §5.2.1 needed no change, having stated the type-relative rule
  correctly all along — the parser was simply not implementing it. Compiler self-hosts
  (stage-3), 415-file sweep unchanged, full suite green. Original analysis follows.
  The parser decides do-bind refutability
  **syntactically**: `is_irrefutable_do_bind_pattern` admits only var/wildcard/unit/tuple-of-those,
  and every `ConstructorPattern` falls to `| _ -> false`. But spec §5.2.1 (`docs/spec-v0.md:203-210`)
  defines refutability as a property of the pattern *versus its type*, which is what W5 applies
  (`infer.sprout:3074-3079`). A `wrap` or 1-ctor ADT pattern is irrefutable by the spec and
  refutable by the parser, so it is closed off both ways: without `else` the parser rejects it
  ("refutable `<-` binding in a do block requires an `else`"), and with the `else` the parser
  demands, W5 rejects the now-dead else arm ("Unreachable match branch") — correctly, since the
  spec says an `else` on an irrefutable pattern is an error. **The two diagnostics point in
  opposite directions, so no error message leads the user to the workaround.** Affects `<-` and
  do-`let`, constant-else and binding-else alike. W5 is not the bug and must not be changed; the
  parser's over-approximation is the sole defect. Note `BACKLOG.md` §1's own entry for the change
  that introduced this gate describes it as complete and correct and never noticed that
  single-ctor destructuring is irrefutable and gets swept up. **Fix:** for a no-`else` do-bind
  whose pattern is a `ConstructorPattern`, route it through the existing `build_do_refutable`
  staircase with a single success arm and no else arm; W5's exhaustiveness check then decides
  refutability type-relatively for free — a single-ctor pattern is total and passes, a genuinely
  refutable one gets the ordinary non-exhaustive-match error, which is also a better diagnostic
  than today's parse error. `do_bind_captures` stays untouched (it never sees the constructor
  pattern), and the syntactic gate is demoted to selecting the fast path rather than deciding
  legality.

- [x] `P3` **Spec §7's effect rules claimed enforcement the checker does not perform. DOC HALF
  FIXED 2026-08-13** — the review's F7 (effect rows inert, `unify_effects` has zero call sites) is
  already tracked as an implementation item above (`merge_effects`, D2/W6). What was *not* tracked
  is that `docs/spec-v0.md` §7 stated rules 8/9/11 as if they held. Verified by running the
  checker: `fn shout(s: String) -> Unit = print(s)` is accepted and its type printed as
  `String -> Unit`. §7 now carries an enforcement note saying effects are parsed, carried and
  printed but never unified, so an annotation is documentation rather than a checked contract —
  with the exception, also verified, that rule 10's effect-polymorphic `main` check *is* enforced
  (`Executable entrypoint `main` must not be effect-polymorphic`). §6's builtin bullet
  cross-references the note. The implementation half stays open under D2/W6.

### 2) Networking and HTTP Client

- [ ] `P2` **The epoll poller collapses two interests on one fd; kqueue does not.** Green-threads
  review 2026-08-13, finding 3. `sprout_poll_add(fd, interest, token)` promises a per-`(fd, interest)`
  registration, and only kqueue delivers one: `EVFILT_READ`/`EVFILT_WRITE` are independent knotes with
  independent `udata`. The epoll backend keys purely by fd, so a second `sprout_poll_add` on an fd
  another task already registered does `EPOLL_CTL_MOD` and silently replaces both the event mask and
  `data.ptr` — the first task is registered nowhere, stays on `g_io_head`, and is never woken again
  (hang, or a bogus "deadlock" abort). `sprout_poll_remove(fd, interest)` compounds it: it ignores
  `interest` and `EPOLL_CTL_DEL`s the whole fd, so even sequential use lets one task's teardown destroy
  another's registration. **Not reachable from today's stdlib** — `http_server` and the HTTP client each
  keep exactly one task per socket — so this is latent, not live. It becomes live the moment a user
  writes a duplex protocol with separate reader and writer tasks on one connection, and the symptom is
  a Linux-only silent hang with no diagnostic, on a program that works on macOS. The one-task-per-fd
  assumption is currently stated only in a comment at the top of `runtime/sprout_poll.c`.
  **Fix (needs a call): make the assumption enforced rather than documented** — a per-fd owner table in
  the scheduler that loud-fails on a second concurrent registration, so the violation is identical and
  immediate on both backends. The alternative (make epoll genuinely per-interest by keeping a per-fd
  pair of tokens and recomputing the mask on every add/remove) is more code and buys a capability
  nothing asks for yet. Prefer the assert until something needs duplex.

- [ ] `P2` **The pump only polls when the ready queue is empty, so one busy task starves all I/O.**
  Found during the same review. `pump_loop` reaches `sprout_poll_wait` only on `rq_pop() == NULL`, so a
  single perpetually-runnable task — anything looping on `task_yield`, which is the documented
  cooperative idiom — keeps the ready queue non-empty forever and no fd readiness or timer is EVER
  observed. `task_sleep` never wakes; deadlines never fire; parked connections never resume. This is
  already known in one place and worked around locally rather than fixed: `tcp_accept`'s EMFILE comment
  rejects a `task_yield` back-off for exactly this reason ("a yield loop would keep this task runnable
  forever and the handlers whose completion frees descriptors would never wake — a livelock"). Treating
  it as a property of the scheduler rather than a hazard each call site must dodge would remove that
  whole class of workaround. **Fix (needs a call):** poll with a zero timeout on some cadence even when
  the queue is non-empty — every N pump iterations, or whenever a poll has not run for some interval —
  so readiness and timers are observed under sustained load. Cheap on both backends (`kevent` with a
  zeroed `timespec`, `epoll_wait(..., 0)`); the cost is one non-blocking syscall per cadence, and the
  cadence is the only real design question. Pairs naturally with the timerfd-free backend already
  scoped below (one shared timerfd + a deadline heap), which needs a timeout-driven wait anyway.

- [x] `P0` Add builtin: `http_request(method, url, headers, body, timeout_ms) -> Result HttpError HttpResponse`.
- [x] `P0` Define `HttpResponse` shape (`status`, `headers`, `body`) and `HttpError` variants.
- [x] `P1` Add convenience wrappers in `stdlib/http_client.sprout` (`get`, `post`, header helpers).
- [x] `P1` Ensure interpreter/native parity for HTTP client builtins.
- [x] `P0` Add outbound TCP client connect primitive (`tcp_connect(host, port)`) for external services such as databases.
- [x] `P0` Add exact-read and write-all socket operations suitable for framed protocols.
- [x] `P1` Define transport failures for socket operations as typed `Result` values instead of only runtime-fatal builtin errors.
- [x] `P1` **Request-param access (low-level layer). LANDED.** New `stdlib/url.sprout` (byte-level percent/query decoding — `percent_decode`/`query_decode`/`parse_query`; builtin-free, multi-byte-correct, loud on malformed) + `query_*`/`form_*` accessors on `stdlib/http_server.sprout` (`query_string`, `query_pairs`, `query_param`, `query_param_all`, and `form_*` gated on `application/x-www-form-urlencoded`). `_param` = first value (Go `url.Values.Get` / Werkzeug `MultiDict.get`); `_param_all` = all; `_pairs` = ordered dup-preserving source of truth. Design: `docs/http-request-params-v0.md`. Tests: `test_url_decode`/`test_query_params`/`test_form_params`.
- [ ] `P2` Request-param **convenience layer** (follow-up to the low-level layer above). Add a merged `param(name, req) -> Maybe String` / `param_all` bag over query+form (precedence: query-first, matching Werkzeug's `CombinedMultiDict([args, form])` order) — the Sinatra/Rails idiom — plus first-wins `Dict String` whole-bag projections `query_params`/`form_params`/`params` for iteration by unique key. All built on top of the existing `_pairs` accessors. See `docs/http-request-params-v0.md` §7.
- [ ] `P2` **Path / route params** (`/users/:id`). Requires changing `Route` from exact `route_path == path` matching to pattern matching, a segment-capturing matcher, and threading captured values into dispatch (a new `HttpRequest` field or a `Dict String` of captures passed to the handler). Then a `path_param(name, req)` accessor. Note `params` is overloaded across frameworks (Sinatra = merged bag, Express = path only); reserve `path_param` for this. See `docs/http-request-params-v0.md` §7.
- [x] `P1` **http_server idle-connection timeout (concurrency review C3) — timeout half DONE
  2026-08-10.** A connection that was accepted but never sent data parked its handler in
  `read_until_headers` forever: the task never completed and `conn` was never closed, so a client
  holding 2048 silent sockets exhausted the runtime handle table until `tcp_accept` could serve
  nobody (slowloris, exploitable with `connect` and `sleep` alone). Both read phases are now bounded
  by a **total** deadline per phase (`ServerConfig`: header 10 s / body 30 s by default, nginx's
  `client_header_timeout` / `client_body_timeout` split), after which the server answers **408** and
  closes. Total, not per-read, is the point — a per-read timer is renewed forever by a peer dribbling
  one byte per interval. New primitive `tcp_read_avail_timeout` + scheduler `PARK_FD_TIMER`; the task
  is NOT dropped on expiry, so the linear `close` still runs. Rejected the `with_timeout` route for
  exactly that reason (a force-dropped task leaks the handle it was meant to protect) plus its cost
  of a green stack per connection. Regression: `tests/task_io_smoke/http_idle_timeout.spr`.
  Prior art verified: Go `SetReadDeadline`, Java `SO_TIMEOUT`, Erlang `gen_tcp:recv/3` all keep the
  socket valid and report the timeout; only Tokio-style `timeout` cancels.
  **Follow-up landed 2026-08-10:** the deadline alone bounded only an IDLE peer. A peer that streams
  bytes without ever sending the header terminator keeps every read succeeding, so no deadline could
  fire and the header accumulator grew for the whole budget (gigabytes on a fast link, quadratic
  copying) — worse than the original slowloris. Fixed by `max_header_bytes` (64 KiB default, 431 per
  RFC 6585; cf. Go's `DefaultMaxHeaderBytes = 1 << 20`) plus an explicit budget check in the read
  loops rather than one inferred from `Err TcpTimeout`. Regression:
  `tests/task_io_smoke/http_header_flood.spr`, verified RED against the pre-cap server. The same
  review found that the fixture for the original fix could not fail — its failure text embedded the
  marker the recipe greps for — which is fixed and negative-control-checked.
- [x] `P1` **http_server: the response WRITE was an unbounded park. FIXED 2026-08-10.**
  `write_and_close` -> `write_all_utf8` -> `tcp_write_all` parked via `scheduler_park_on_fd` with no
  deadline. A client that sent a valid request for a response larger than the socket send buffer and
  then stopped reading without closing wedged its handler forever: the task never finished,
  `close(conn)` never ran, the `g_conn_used` slot was never freed. 2048 such clients and `tcp_accept`
  served nobody — the same handle exhaustion the read deadline closed, reached from the write side,
  and the 408/431 error paths travel the same `write_and_close`. New builtin
  `tcp_write_all_timeout` reusing `PARK_FD_TIMER` with `SPROUT_POLL_WRITE`, `net.write_all_timeout` /
  `write_all_utf8_timeout`, and a fourth `ServerConfig` field `write_ms` (30 s default) threaded
  through `write_and_close`. Bound is **idle**, not total: nginx `send_timeout` is "set only between
  two successive write operations, not for the transmission of the whole response" (verified), so any
  accepted byte re-arms it and a slow-but-reading client is never cut off. Regression:
  `tests/task_io_smoke/http_write_timeout.spr`, which asserts on **truncation** rather than
  termination — a client that merely slept and closed would let the unfixed server finish too, since
  its close resets the connection. Verified RED against the unbounded write (whole 8388697-byte body
  delivered). What this deliberately does **not** bound, matching nginx: a peer that keeps reading a
  trickle holds its connection as long as it likes.
- [x] `P2` **http_server body deadline was total wall-clock. FIXED 2026-08-10 (now idle).**
  `continue_read_request` armed `now + config_body_ms` for the whole body, so a legitimate slow upload
  was cut off mid-transfer: a 100 MB POST over a 3 Mbit/s uplink needs ~280 s and died at 30 s with a
  408, discarding the partial body — every large upload over a link slower than ~27 Mbit/s. The doc
  comment claimed to follow nginx's split, and for the header phase it did (`client_header_timeout`
  **is** total: *"If a client does not transmit the entire header within this time, the request is
  terminated"*), but `client_body_timeout` is explicitly *"set only for a period between two successive
  read operations, not for the transmission of the whole request body"*. `read_remaining_body` now
  re-arms its deadline from `body_ms` on every chunk that delivered bytes; the header phase stays
  total, which is the bound a dribbling slowloris cannot renew. A flooding body peer is bounded by
  `content_length` instead — the loop only asks for the bytes the request promised — which is why it
  needs no size cap of its own the way the header loop does. Regression:
  `tests/task_io_smoke/http_body_timeout.spr` (four 5-byte chunks 200 ms apart against a 300 ms
  budget), verified RED against the total deadline. **SUPERSEDED 2026-08-10 — the idle body deadline
  is gone.** It bounded a peer that STOPS but not one that merely crawls: a byte per interval re-armed
  it forever, so handler occupancy had no upper limit, which blocked the bounded worker pool
  (docs/green-task-pool-v0.md §5.2). The body deadline is now TOTAL but scaled by the announced size
  — `body_ms + content_length / min_rate_bps` — which is Apache's `RequestReadTimeout body,MinRate`
  in closed form, HTTP stating the length up front. The max body size (413) landed with it, and had
  to: a size cap bounds BYTES not TIME (1 MiB at a byte per interval stays under any cap forever),
  and a rate floor without a cap has no finite quantity to divide. Regression:
  `tests/task_io_smoke/http_body_bounds.spr`.
- [ ] `P2` **A timed read costs a timerfd per park on Linux** (code review of PR #56, 2026-08-10).
  **The fatal half is FIXED 2026-08-10; the cost remains.** `sprout_poll_add_timer` calls
  `timerfd_create` per registration, so every bounded read/write park allocates a descriptor,
  doubling the per-connection fd cost. Arming failure used to be `sprout_fail`: with `ulimit -n 1024`,
  ~500 concurrently parked reads exhausted the table and the next read aborted the process, dropping
  every in-flight connection — a slowloris crashed the server at roughly half the previous
  concurrency instead of being answered 408. `sprout_poll_add_timer` now returns success/failure and
  the caller decides, split by whether an honest degradation exists:
  `scheduler_park_on_fd_timeout` reports a timeout (the server sheds that one connection and frees
  descriptors), while `task_sleep` / `with_timeout` still fail loudly, because there is no answer to
  give and returning early would silently break the only guarantee either makes.
  **Second consequence, found 2026-08-11 during the C3 accept fix:** because `task_sleep` is fatal when
  it cannot arm, *no Sprout code can back off while descriptors are exhausted* — which is exactly when
  a server most wants to. The C3 retry therefore had to live in C, parking on the listener fd. A
  bounded, Sprout-side accept policy wants a **listener readiness primitive**
  (`tcp_listener_wait(listener, ms)` over `scheduler_park_on_fd_timeout`, which already degrades to
  "not ready" instead of dying when the timer cannot be armed) — `tcp_wait` only accepts *connection*
  handles, so listeners have no readiness primitive at all today. That is a new builtin and needs
  approval; it would also let the accept retry be bounded rather than indefinite.
  **DEFERRED after scoping it 2026-08-11 — do not build this as one small builtin, the sketch above
  does not close.** Three findings, in order of how much they change the plan:
  1. **It needs TWO builtins, not one.** `tcp_listener_wait` itself is trivial (~7 lines mirroring
     `tcp_wait`, which is just a handle check plus `scheduler_park_on_fd_timeout`). But the obvious
     consumer — `accept_timeout` = wait-for-readiness, then accept — is *not* bounded, because
     `tcp_accept` PARKS INDEFINITELY on EAGAIN by design (that is the C3 fix). If another task wins
     the race between the readiness signal and the accept, the "bounded" call parks forever, so the
     API would be lying about its only guarantee. A genuinely bounded version also needs a
     non-parking accept (`Err TcpWouldBlock` instead of a park), the way `tcp_read_some` relates to
     the parking read. Two new pieces of host surface, not one.
  2. **The motivating case is already solved, in C.** This item's own stated driver was backing off
     under descriptor exhaustion, and `tcp_accept` now absorbs that by parking on the listener fd and
     retrying. So the primitive would ship with NO consumer — and "keep host-side builtins minimal"
     (AGENTS.md, Builtin vs Stdlib rule 4) is exactly the rule against that. A shutdown-polling use
     case does not motivate it either: `scope_cancel` already force-drops a parked `serve_forever`
     (`tests/task_io_smoke/http_serve_forever.spr` covers it).
  3. **Its full value is gated on the timerfd-free backend below anyway.** Until that lands, every
     bounded wait costs a descriptor on Linux, which is precisely the resource an exhaustion back-off
     does not have — the reason the C3 retry had to live in C in the first place.
  **What would make it worth building:** a concrete caller that must give up on accepting (a
  supervisor rebinding a listener, a drain-then-rebind reload), and the timerfd-free backend so the
  wait is descriptor-free. Then do both builtins together with that caller in the same change.
  **Remaining work — the timerfd-free backend**: one shared timerfd plus a deadline heap (or simply
  the `epoll_wait`/`kevent` timeout argument driven by a min-heap of deadlines), which is what a
  production reactor does. It would make `task_sleep` descriptor-free and dissolve the problem above. It removes the per-park descriptor entirely on both backends and would
  also **delete the `Task.park_timer_dead` exactly-once dance**, which exists *only* because a
  timerfd close must happen exactly once. Deliberately not bundled with the review fixes: it rewrites
  timer semantics for `task_sleep`, `with_timeout` and `select` across kqueue and epoll at once, and
  kqueue (`EVFILT_TIMER`, no descriptor) cannot exercise the regression locally.
- [x] `P2` **A timed-out read could report 408 with a complete request sitting in the socket buffer.
  FIXED 2026-08-10.** `tcp_read_avail_timeout` returned `Err TcpTimeout` on the timeout path *without*
  a final non-blocking `recv`. If the readiness event and the timer event landed in the same poll
  batch and the timer was drained first, `scheduler_park_on_fd_timeout` returned 0 and the server
  answered 408 to a client whose valid request had arrived well inside its budget. Go's
  `SetReadDeadline`, the cited model, attempts the syscall first and prefers buffered data to the
  deadline error. Fixed structurally rather than by adding a second `recv` call: the collapsed core
  (below) carries a `deadline_expired` flag and `continue`s, so **every** return path passes through
  the loop's `recv` first. Same treatment on the write side (`tcp_send_all` always attempts the
  `send` before reporting a stall).
- [x] `P3` **`tcp_read_avail_timeout`'s spurious-readiness retry re-parked with the FULL budget.
  FIXED 2026-08-10.** On readable-then-EAGAIN the loop re-parked for another whole `timeout_ms`
  instead of a re-derived remaining slice, so one call was bounded only best-effort and a peer able to
  trigger readiness without delivering bytes could hold a handler past its phase budget. The collapsed
  core now computes one `deadline_us` at entry from `time_now_micros()` and parks with the remaining
  slice each round (rounding a sub-millisecond remainder up to 1 ms, so it never parks on 0). This
  also subsumed the old special-cased `timeout_ms <= 0` poll-once branch: an already-expired deadline
  simply fails the remaining check after the first `recv`. Covered by
  `tests/task_io_smoke/read_timeout_poll_once.spr`.
- [ ] `P3` **A force-dropped handler still leaks its connection** (code review of PR #56,
  2026-08-10; PRE-EXISTING, not introduced by that PR). `scope_cancel` / an enclosing `with_timeout`
  force-drops a handler parked in a socket read; `force_drop_task` tears down the poller
  registrations and frees the green stack, but `park_close_fd` is -1 for a handle-table-owned conn,
  so the fd is never closed and `g_conn_used[conn]` stays 1 (the 64 KiB read buffer on the freed
  stack leaks too). Repeated cancel-and-restart cycles in one process exhaust the 2048-entry table.
  The linear `close` cannot help — an out-of-band drop runs no Sprout code. Fix direction: let a
  parked task register a cleanup for a table-owned handle (a generalization of `park_close_fd`), or
  have the scope own connection handles so join-time reclaim covers them.
  **Investigated 2026-08-10 while fixing the other seven findings from that review; deliberately NOT
  implemented, because the obvious fix is worse than the leak.** Extending `park_close_fd` to also
  clear `g_conn_used[conn]` converts a leak into a **handle-reuse hazard**: the slot is immediately
  reallocatable, so any surviving Sprout `TcpConnection` value naming it now denotes a *different*
  peer's connection, and a later `close` on it either shuts down an unrelated live connection or —
  if the slot is momentarily free — hits `tcp_close`'s unknown-handle path, which is `tcp_fail`, i.e.
  process death. A leaked slot is bounded and silent; a reused one is a cross-connection data-integrity
  bug. The runtime cannot tell the two apart on its own: it would have to know the Sprout-level owner,
  and force-drop by construction runs no Sprout code. Linearity *nearly* supplies the missing
  guarantee — a borrow cannot cross `task_spawn`, so within the linear API a parked conn is owned by
  the parked task — but the raw-`Int` `tcp_*` escape hatch that `stdlib/net.sprout` documents ("callers
  on this path get no release enforcement") is exactly where the assumption fails, and that hatch is
  the shape most likely to hold a handle across tasks. So the finding's **second** option is the real
  one: make the scope own connection handles, so join-time reclaim covers the value *and* the slot
  together and no stale name survives. That is a design task, not a patch. Cheap safe subset available
  meanwhile if this ever bites: free the parked 64 KiB `recv` buffer on drop (a plain `malloc` scratch
  buffer nothing else references), which needs no ownership reasoning at all.
- [x] `P3` **Body-phase timeout and the poll-once budget path were untested. FIXED 2026-08-10.** The
  smoke fixtures drove `read_until_headers` only. Nothing covered a client that completes its headers
  with a `Content-Length` then stalls (`continue_read_request` -> `read_remaining_body` ->
  `Err TcpTimeout`), nor the `timeout_ms <= 0` poll-once branch — and the body phase carries the longer
  default, so its failure mode is the longest-lived handler leak. Added
  `tests/task_io_smoke/http_body_timeout.spr` (stalled body -> 408, plus the dribbling client that
  distinguishes idle from total) and `tests/task_io_smoke/read_timeout_poll_once.spr` (poll-once
  reports a timeout without parking; a live budget still delivers data that arrives inside it — the
  half that catches a "fix" reporting `TcpTimeout` unconditionally).
- [x] `P3` **Three near-verbatim clones of the recv/park/adopt loop. FIXED 2026-08-10.** `tcp_read`,
  `tcp_read_avail` and `tcp_read_avail_timeout` duplicated the same GC-sensitive sequence
  (`maybe_collect_threshold`, `malloc(65537)`, `recv`, park on EAGAIN, `buf[n]='\0'`, `adopt_cstr`,
  errno captured before `free`), differing only in the park call and the error convention — and the
  copies had already drifted (`tcp_read` aborted where `tcp_read_avail` returned `Err`). Collapsed to
  one static `tcp_recv_avail` core (a `TcpWaitMode` selects untimed-vs-deadline; the outcome is a
  three-way enum) with three thin entry points. Doing this **first** is what made the two behavioural
  fixes above one-line changes instead of three-way edits, and it is why the missing-final-`recv` fix
  came out structural. The write pair got the same treatment (`tcp_send_all`), so the new
  `tcp_write_all_timeout` did not arrive as a fourth clone of what was just deduplicated.
- [ ] `P2` **Decompose the timed `tcp_*` builtins into ONE readiness primitive plus Sprout-level
  loops.** Raised and scoped 2026-08-10, when `tcp_write_all_timeout` was approved as the *interim*
  fix for the unbounded response write. The current design adds **one timed twin per operation**, and
  the queue is already visible: `tcp_read_exact_timeout` is required by the body-framed-by-bytes item
  below (the body phase is deadline-bounded and must move to `tcp_read_exact`). The http_client
  blocking-connect item no longer drives a `tcp_connect_timeout` twin — it was fixed (2026-08-12)
  with an internal C helper plus one scheduler entry point, adding no Sprout-visible builtin — but
  it did add a *third* private park/retry loop over the same dance, which is more evidence for this
  decomposition, not less. Each twin is ~25 lines of C
  duplicating the park/retry dance — i.e. the clone problem just deduplicated above, regenerating
  faster than it can be collapsed.
  **Shape:** separate *readiness* from *transfer*, as every reactor does.
  `tcp_wait(conn, interest, ms) -> Result TcpError Bool` parks until ready or the deadline and moves
  no data — one builtin, covering read, write, connect and accept forever. The transfer primitives
  stop parking internally and report `Err TcpWouldBlock`; the retry-and-deadline loops move into
  `stdlib/net.sprout`, written once. Aligns with "Builtin vs Stdlib" rules 4-6: keep in C only what
  cannot be expressed in Sprout (parking is scheduler-internal), and put every composable policy above
  it in the language.
  **It dissolves three of the eight PR #56 review findings rather than fixing them**: the cloned
  recv/park loops become one Sprout loop; the remaining-slice arithmetic becomes testable Sprout
  instead of an invariant buried in C; and "attempt the transfer, then wait" becomes the loop's only
  possible shape, so the missing-final-`recv` class cannot recur. The reason those were three separate
  C bugs is that timeout policy and syscall retry are welded together inside each builtin.
  **Costs to design around:** it is a breaking change to the `tcp_*` extern surface, which
  `stdlib/net.sprout` documents as an escape hatch for shapes the linear API cannot express;
  `tcp_write_all` needs an offset (`tcp_write_some(conn, data, offset)`) or the Sprout-side partial-write
  loop re-slices `Bytes` per chunk and goes quadratic; and `read_exact`'s accumulate loop moves from one
  in-place C buffer fill to per-chunk allocation in Sprout, which must be **measured**, not assumed.
  When this lands, delete every `tcp_*_timeout` twin and its `APPROVED_BUILTINS` entry.
  **STARTED 2026-08-10: the two primitives now exist.** Bounding the response write needed exactly
  this split — `tcp_write_all_timeout` re-arms its idle bound in C on every accepted byte, so a peer
  taking one byte per interval held a handler forever and no deadline computed in Sprout could
  intervene. Rather than add the fourth twin this entry warned about, `tcp_wait(conn, interest, ms)`
  (readiness, moves no data) and `tcp_write_some(conn, data, offset)` (transfer, never parks, reports
  `Err TcpWouldBlock`) went in, with the retry-and-deadline loop in `stdlib/net.sprout` as
  `write_all_by`. `offset` is there for the quadratic-reslicing reason predicted above. **Remaining:**
  migrate `tcp_read_avail_timeout` and `tcp_write_all_timeout` onto `tcp_wait` and delete them, plus
  the `tcp_read_exact_timeout` / `tcp_connect_timeout` twins this makes unnecessary. The read side is
  unmigrated only because it was already expressible — `read_avail_timeout` returns per chunk, so
  http_server could compute its own deadlines around it.
  **2026-08-11: the motivation is now much stronger than duplication, and the plan is concrete.** The
  String-returning reads are a **soundness** defect, not just clone debt: they violate the `String`
  invariant from `docs/spec-v0.md` (valid UTF-8, no NUL byte) via
  `sprout_gc_adopt_cstr(buf, n, …)`, which is W2 R2 — see the `P1` entry above, and note this
  reduction IS the execution of decision D4 ("Bytes-primary via the `bytes_to_utf8` choke point") for
  `net`. Target surface, measured against actual callers:
  | Keep | Add | Delete |
  |---|---|---|
  | `tcp_wait` (readiness), `tcp_write_some` (transfer) | `tcp_read_some` — non-parking, `Err TcpWouldBlock`, mirroring `tcp_write_some` | `tcp_read`, `tcp_read_avail`, `tcp_read_avail_timeout`, `tcp_read_exact`, `tcp_write_string` |
  Five read builtins are not five capabilities — they are one capability with four policy combinations
  hard-coded in C (*park forever vs. deadline* × *whatever's available vs. exact count*). `http_server`,
  the only production consumer, uses **exactly one** of them.
  **The READ half landed 2026-08-11** (see the `P1` W2 R2 entry above for the full account): added
  `tcp_read_some`; deleted `tcp_read`, `tcp_read_avail`, `tcp_read_avail_timeout` and `tcp_echo_serve`;
  `read_avail_timeout` is now a Sprout loop over `tcp_wait` + `tcp_read_some`, and `read_avail` was
  deleted outright rather than rebuilt (`tcp_wait` takes a finite `ms`, and an unbounded socket read is
  the hazard being removed). Net -4 builtins.
  **Remaining, and deliberately deferred rather than forgotten:**
  * `tcp_read_exact` — NOT a W2 R2 violator (`Bytes` in, `Bytes` out), so it was out of scope for the
    soundness fix. Rebuilding it over `tcp_wait` is still worth doing, because it has **no deadline**:
    it parks indefinitely, which is why the C5 body-framing fix could not use it. That change adds a
    deadline parameter, so it is an API change and belongs in its own PR.
  * `tcp_write_all_timeout` — the write-side twin, same argument as the reads: it re-arms its idle
    bound inside C, so a caller can never impose a total bound. `write_all_by` already shows the shape.
  * `tcp_write_string` — kept, per its own stated retirement condition (go with the raw `tcp_*`
    externs, not before). The raw family now blocks on exactly one thing: a task force-dropped by
    `scope_cancel`/`with_timeout` never runs its linear `close`, so the cancellation fixtures cannot
    hold a `TcpConnection`. That is the `P2` entry below, which is therefore this family's blocker.
- [x] `P1` **http_server accept-failure isolation (concurrency review C3, remaining half).
  FIXED 2026-08-11.** `tcp_accept` had two `tcp_fail` paths — any errno other than EAGAIN, and a full
  connection table — and both `exit(1)`. It now returns `Result TcpError Int`. No new builtin, and no
  change to the C prototype or the emitted `declare` (a `Result` is an i64 heap box under the
  i64-uniform ABI, so `-> Int` and `-> Result TcpError Int` are the same at the LLVM boundary).
  **The classification is wider than this entry predicted, and that is the substantive finding.** It
  named `ECONNABORTED`/`EINTR` as the retryable pair. accept(2) documents **eight more**: *"Linux
  accept() (and accept4()) passes already-pending network errors on the new socket as an error code
  from accept(). This behavior differs from other BSD socket implementations. For reliable operation
  the application should detect the network errors defined for the protocol after accept() and treat
  them like EAGAIN by retrying. In the case of TCP/IP, these are ENETDOWN, EPROTO, ENOPROTOOPT,
  EHOSTDOWN, ENONET, EHOSTUNREACH, EOPNOTSUPP, and ENETUNREACH."* So on Linux a peer whose route
  disappeared between SYN and accept was killing the server — and the man page flags this as
  Linux-specific, i.e. **invisible on macOS, where every local gate runs**. ENONET is Linux-only and
  is `#ifdef`-guarded.
  Once those retry, the remaining error bucket is `EBADF`/`EINVAL`/`ENOTSOCK` — none of which heal —
  so the "back off and retry N times" shape this entry proposed for them was dropped as pointless.
  Final split: **everything transient is absorbed inside the builtin** — EAGAIN parks;
  EINTR/ECONNABORTED/the eight continue; EMFILE/ENFILE **and** a full handle table park on the
  listener fd and retry. Only `Err TcpAcceptFailed` reaches Sprout, and it is reported at once.
  **The exhaustion retry had to be in C, and CI is what established that.** The first revision put a
  back-off in Sprout with `task_sleep(100)`, which passed every local gate and failed on CI:
  `runtime error: builtin task_sleep: could not arm a timer (descriptor exhaustion?)`. `task_sleep`
  arms a **timerfd** on Linux, so the recovery path needed the very descriptor that had just run out,
  and its arming failure is deliberately process-fatal (see the timerfd entry above — "there is no
  answer to give"). macOS arms kqueue timers on the existing kqueue and needs no descriptor, so it was
  invisible locally — the same platform asymmetry as accept(2)'s pending-network errnos, twice in one
  change.
  `task_yield` is not an alternative either: `pump_loop` polls **only when `rq_pop()` returns NULL**, so
  a yield loop keeps the accept task runnable forever and the handlers whose completion frees
  descriptors never wake — a livelock, worse than the abort being replaced. The wait must be on the
  listener fd, and no Sprout-visible primitive registers one (`tcp_wait` takes a *connection* handle).
  So this is not a policy-in-C regression from the read-side reduction: for reads Sprout **could**
  express the wait, here it cannot. Parking costs no descriptor and converges — both backends are
  level-triggered one-shot, so the park returns immediately while connections are pending (draining the
  backlog) and becomes a real wait once drained, while the accepted connections' bounded reads shed on
  their own because `scheduler_park_on_fd_timeout` degrades to reporting a timeout.
  `TcpAcceptExhausted` was therefore **removed** rather than kept: no producer can return it, and a
  variant nothing returns is worse than none.
  Prior art verified against primary sources before choosing: Go's `internal/poll.(*FD).Accept`
  retries EINTR/ECONNABORTED in-loop ("it's a silly error, so try again") and returns EMFILE to the
  caller; nginx's `ngx_event_accept` continues on ECONNABORTED and disables accept events on EMFILE;
  Erlang's `gen_tcp:accept` names exhaustion distinctly (`Reason :: closed | timeout | system_limit |
  inet:posix()`). Named variants rather than one error plus a retryable predicate, because Go
  **deprecated** `net.Error.Temporary()` with the note that *"Temporary errors are not well-defined."*
  **A trap worth knowing, and the reason all three loops use an explicit `match`:** in a `Unit !{IO}`
  function a bare `conn <- accept(listener)` of an `Err` **silently returns early and discards it** —
  verified. Left as-is, an EMFILE would have ended the accept loop quietly and exited **0**: a server
  that stops serving and reports success, harder to diagnose than the `exit(1)` being replaced. The
  typechecker accepts the bare bind, so nothing but convention prevents it. See the `P2` below.
  Tests: `tests/task_io_smoke/tcp_accept_bad_handle.spr` (deterministic, no timing — the load-bearing
  coverage of the contract) and `http_accept_exhaustion.spr` (end-to-end survival under descriptor
  pressure at `ulimit -n 32`). The second is what caught the `task_sleep` mistake, and the redesign
  also made it robust: with the retry in C it passes across `ulimit -n` **16-64**, where the Sprout
  back-off version only reached the exhaustion path in a narrow 32-40 band on macOS.
- [x] `P1` **EMFILE accept hot-spin — the convergence claim above was false (code review finding 9).
  FIXED 2026-08-12.** The C3 entry's "parking converges because the park returns immediately while
  connections are pending, *draining the backlog*" is true for the retryable errnos and the full-handle
  path — each of those **consumes** its queued connection — but NOT for EMFILE/ENFILE: `accept()` under
  descriptor exhaustion returns without dequeuing the pending connection (accept(2)). So the listener
  stays readable, the level-triggered park returns at once, and `tcp_accept` spins at 100% CPU for as
  long as descriptors are exhausted and any connection is pending. The convergence reasoning was copied
  from paths where it holds to one where it does not. Fix: the classic libev **reserve-fd drain**. A
  single process-global reserve descriptor (`g_accept_reserve_fd`, `open("/dev/null", O_CLOEXEC)`,
  armed in `tcp_listen` while fds are plentiful — user-approved runtime addition). On EMFILE,
  `accept_shed_backlog` closes the reserve to free one slot, then accept+close the whole backlog (each
  accept reuses the freed slot, each close returns it, so one reserve drains the queue sequentially;
  continues on the retryable/pending-network errnos so it drains fully in one pass on Linux too),
  reopens the reserve, and only then parks — now a **real** wait because the queue is actually empty.
  Shedding also gives the queued clients a prompt FIN instead of leaving them stalled behind an
  exhausted server. **Lesson:** a "converges" claim is only valid on a path that consumes/sheds; before
  reusing one, check the failure actually dequeues. Deterministic coverage:
  `tests/c_runtime/emfile_accept_shed.c` (lowered `RLIMIT_NOFILE`, no scheduler — asserts the shed
  drains to EAGAIN, sheds >0, and re-arms; verified RED by neutering the shed to a no-op). End-to-end
  survival remains `http_accept_exhaustion.spr`. Gates: c-runtime-test, task-io-smoke, **linux-smoke**
  (epoll+timerfd), canary, full `just test` — all green.
- [ ] `P2` **A bare `<-` bind of a `Result` in a `Unit`-returning function silently discards the
  `Err`.** Found 2026-08-11 while making `accept` recoverable. `fn f() -> Unit !{IO} = do { x <-
  returns_a_result(); ... }` type-checks, and on `Err` it returns early from `f` with the error
  dropped — no diagnostic, and the caller continues as if nothing happened. That is defensible
  do-notation semantics, but it is invisible at the call site and it converts a newly-recoverable API
  into a silently-swallowing one: the whole benefit of returning a `Result` is lost precisely where a
  loop must react to it. It is also undetectable by the typechecker as things stand, so the only
  defence today is a comment. **Wanted:** a warning (or error) when a `<-` bind's `Err` is discarded
  in a non-`Result` context, of the shape "this bind can fail and the failure is unused — `match` it,
  or make the function return a `Result`". Cheap version: warn whenever the bound expression's type is
  `Result _ _` and the enclosing function's return type is not.
  **DESIGNED 2026-08-11 — awaiting a call on severity. Full proposal, measurement and verified
  prior-art survey: [docs/fallible-bind-diagnostic-v0.md](./docs/fallible-bind-diagnostic-v0.md).**
  Headline numbers: of **1539** `<-` binds in the tree, **21** discarded fallible binds use `_ <-`
  (deliberate — and `_ <-` is exactly how GHC suppresses its equivalent warning) and only **5** use a
  named binder, of which **1** was production code. That one was a live defect and is **fixed**:
  `run_check_iface` discarded `read_file`'s `Err`, so `--check-iface` printed nothing and exited 0 for
  a file it never read, defeating the exit-status contract documented directly above it; regression in
  `just diagnostic-stream-smoke`. Survey (each row verified against a primary source) is unanimous
  that this is a **warning, never an error**, with an underscore call-site opt-out: Rust
  `unused_must_use` warn-by-default + `let _ =`; GHC `-Wunused-do-bind` off-by-default + `_ <-`;
  Swift SE-0047 warn-by-default + `_ =`. Recommended implementation is a **driver-side lint pass**,
  not an `infer` change — see the doc for why the obvious route costs a change to the compiler's core
  result type. Remaining blockers are decisions, not code: warn vs error, and whether CI gates on it.
  Findings kept below, since they are the reason the doc exists:
  - `compiler.Diagnostic` already has a `DiagWarning source.SourcePos String` arm, and four drivers
    already render it (`compile_driver` prints `WARNING:` to stderr without setting the failure flag;
    `full_driver`, `lsp_driver` — as LSP severity 2 — and `analysis_service_driver` all handle it).
    It looks ready. **It is constructed NOWHERE.** Every one of those is a consumer of a producer that
    has never existed, so the rendering path is unexercised scaffolding, not working infrastructure.
  - Worse, there is no channel for a warning on a *successful* compile: `CompileResult` is
    `CompileOk (Dict types.Scheme) | CompileFail (List Diagnostic)`, so warnings can only be
    delivered by FAILING. Emitting one from `infer` therefore means adding a diagnostics list to
    `CompileOk` and threading it up through `checker.CheckOk` and `InferResult` — a change to the
    compiler's core result type touching ~10 pattern sites across four drivers, plus inference.
  - **A much smaller complete design exists: lint in the DRIVER, not in `infer`.** After a successful
    check, `compile_driver` holds the typed env (`CompileOk (Dict types.Scheme)`) and can already
    recover the AST (`parse_for_decls`). A standalone pass — walk each `FnDecl` body for `do` bind
    steps, resolve the bound expression's callee in the env, warn when its return type is `Result _ _`
    and the enclosing `fn`'s declared return type is not — needs NO change to `CompileResult`, no
    threading through inference, and finally gives `DiagWarning` its first producer. It only handles
    binds whose RHS is a named call, which is the reported shape; other RHS forms are a known gap
    rather than a wrong answer.
  - **Two decisions must be made before implementing, and they are the real blockers, not the code.**
    (a) *Warn or error?* The construct is legal do-notation, so an error breaks working code.
    (b) *What about the existing hits?* Nobody knows how many stdlib/compiler sites the rule would
    flag, and each needs triage: some deliberately ignore a failure, and a rule that fires dozens of
    times on landing gets suppressed rather than fixed. Measure the hit count with a throwaway pass
    BEFORE choosing (a).
    Per AGENTS.md a new diagnostic is a language change and goes through the Design Change Process —
    including a prior-art survey of how comparable languages treat a discarded fallible bind (Rust's
    `#[must_use]`/`let _ =`, Haskell's `-Wunused-do-bind`, Swift's `@discardableResult`, Go's errcheck
    linters) — so it needs an approved design, not just an implementation.
- [x] `P1` **http_server framed request bodies by codepoint count, not bytes (concurrency review C5).
  FIXED 2026-08-10.** `err_or_request` / `continue_read_request` / `read_remaining_body`
  (`stdlib/http_server.sprout`) compared `string.length` (UTF-8 codepoint count) against the
  byte-denominated `Content-Length` header, while the response side correctly used
  `string.byte_length`. A body with any multi-byte character (`café`, `Content-Length: 5`, 4
  codepoints) was mis-framed in BOTH directions: declared short, or over-read by a byte that on a
  pipelined connection belongs to the next request. Body framing is now byte-exact
  (`bytes.slice` over `bytes.from_string(raw)`, offset derived via `byte_length` of the header
  prefix so a non-ASCII header value cannot shift it), and the read loop subtracts
  `string.byte_length(chunk)`. The write deadline's rate allowance was denominated in codepoints
  too and is now bytes. Tests: 4 checks in `tests/stdlib/test_http_server_parse.spr` (under-read,
  over-read, split codepoint, non-ASCII header value) + `tests/task_io_smoke/http_utf8_body.spr`
  (read-loop path, already-buffered path, split-codepoint liveness).
  - **The prescribed fix in this entry was wrong, and the reason is worth keeping.** It said to read
    exactly `content_length` bytes via `tcp_read_exact`. That builtin has **no deadline** — it loops
    on `recv` and calls `scheduler_park_on_fd` on `EAGAIN` with no timer (`runtime/sprout_runtime.c`
    `tcp_read_exact`) — so following it would have fixed the framing by reinstating unbounded handler
    occupancy, the exact thing that had to land before `serve_pooled`. This entry predates the
    occupancy work and was never reconciled with it. The fix keeps the deadline-bounded
    `read_avail_timeout` loop and changes only the arithmetic.
  - **`str_slice_bytes` is NOT usable here, and the reason generalises:** it is the byte-indexed
    slice builtin, but on a cut that splits a UTF-8 codepoint it calls `tcp_fail` — the
    process-fatal path — rather than returning an `Err`. A client announcing 4 bytes of a 5-byte
    character would have killed the whole server, and under `serve_pooled` every worker with it.
    `bytes.slice` clamps and `bytes.to_string` validates into a `Result`, at the cost of two
    allocations. See the `json.parse` entry below for the same guard firing as a live remote abort.
- [x] `P1` **`json.parse` ABORTED THE PROCESS on any non-ASCII input. FIXED 2026-08-10.** Every
  dispatch read in `stdlib/json.sprout` is now a byte COMPARISON at an offset
  (`str_starts_with_at_byte`, reads in place, cannot abort) rather than a one-byte slice: `byte1`,
  `in_set`, `is_digit_byte` and `hexval` are gone, replaced by `at_end`, `is_digit_at`,
  `is_num_byte_at` and `hex_at`. The remaining `str_slice_bytes` calls take TOKEN spans, and each is
  codepoint-aligned at both ends by construction — a run stops at an ASCII delimiter or end-of-input
  and starts where the previous stopped — so the abort class is gone from the module rather than
  patched at the sites that happened to be found. `str_char_at` was rejected as the alternative: it is
  codepoint-indexed and O(n) per call, which would make the parser quadratic.
  **Measured, since the module justifies its byte cursor on O(n) grounds:** 20 parses of a
  29,609-byte numbers-heavy document (numbers-heavy because `num_end` was the hottest `byte1` site)
  ran ~106 ms steady-state both before and after — **neutral, not faster.** The prediction was that
  removing an allocation per inspected byte would win; it did not show up, plausibly because a 1-byte
  string allocation is an arena bump and ~15 short-circuiting calls cost about what one
  alloc-plus-`str_find` did. Recorded because this module already has a history of asserting
  unmeasured complexity (see the `str_slice_bytes` entry below).
  Tests: 9 checks in `tests/stdlib/test_json.spr` — 2-, 3- and 4-byte characters, non-ASCII in an
  object KEY, before a comma, at top level, through a stringify round trip, and two that assert the
  decoded value byte-for-byte so a parser that skipped the bytes cannot pass. On the unfixed parser
  the suite binary dies before printing anything (`run_suite` builds its list eagerly), so the result
  count is itself the signal.
  Original report: Verified repro:
  `json.parse("{\"a\":\"café\"}")` prints `runtime error: builtin str_slice_bytes:
  byte_start+byte_len splits a UTF-8 codepoint` and exits 1. Cause: `stdlib/json.sprout` `byte1(s, i)
  = str_slice_bytes(s, i, 1)` slices a single byte for character dispatch; when `i` is the LEAD byte
  of a multi-byte sequence, `bs` passes the builtin's continuation-byte check but `bs + bl` lands on
  a continuation byte, so the guard fires `tcp_fail`. Reachable from
  `stdlib/compiler/lsp_driver.sprout:362` (`json.parse(body)` — opening a Sprout file containing any
  non-ASCII string literal kills the LSP server), `stdlib/compiler/analysis_service_driver.sprout:1047`,
  and any HTTP server parsing a JSON request body — an unauthenticated remote process kill, which
  under `serve_pooled` takes down every worker rather than one connection. **Fix candidates:** `bytes.get`
  (`Maybe Int`, already exists, no new builtin) or widening the `str_starts_with_at_byte` comparisons;
  `json.sprout`'s header comment justifies the byte cursor on O(n) grounds, so the hot dispatch loop
  needs measuring rather than assuming. Consider also whether `str_slice_bytes` should return a
  `Result` instead of aborting — a process-fatal on attacker-controlled offsets is the wrong default
  for a string builtin, and this is the second place it bit (see C5 above).
- [x] `P1` **Swept every text-indexing module for the byte-vs-codepoint blind spot. DONE 2026-08-10 —
  the headline is a NEGATIVE result: no third live bug.** The two fixed above were the only ones in
  `stdlib/`. What the sweep did produce is coverage, which is the part that keeps the axis closed.
  - **Fatal-builtin axis.** Of the ~30 `tcp_fail` sites in text builtins, almost all are null-input or
    OOM guards — unreachable from Sprout, where a `String` is never null. Only **three** are
    data-dependent, i.e. reachable with valid-but-unlucky input:
    1. `str_slice_bytes` split-codepoint (`runtime/sprout_runtime.c:5118`/`:5120`) — `json` FIXED;
       `stdlib/compiler/lexer.sprout:11` is the only other caller and was **verified clean by probe**
       (2-, 3- and 4-byte literals, mixed widths, `Char` literals, and interpolation with non-ASCII on
       both sides of `${}` — `source.cursor_byte_offset` is codepoint-boundary-aligned, so its token
       slices cannot split).
    2. `str_utf8` invalid/truncated UTF-8 (`:4943`/`:4963`) — **unreachable from Sprout**: there is no
       `extern fn` declaration for it, only `char_to_string`/`char_to_str`.
    3. `term_read_key` non-ASCII byte (`:3278`) — live, deliberate, and was unfiled; see below.
  - **Mixing axis.** The load-bearing fact, verified in the runtime rather than assumed: **`str_find`
    returns a CODEPOINT count**, converting its byte offset via `sprout_utf8_step` before returning
    (`:5449`-`:5456`). That is what makes the `str_find` + `str_slice`/`take`/`drop` pairing used
    throughout `http`, `template`, `scram` and `string` internally consistent, so those modules are
    correct for non-ASCII rather than accidentally so. Two more results worth recording:
    - `regex` looked like a repeat of C5 — POSIX `regexec` reports `rm_so`/`rm_eo` as BYTE offsets and
      `split_first` feeds them to codepoint-indexed `str_slice` — but the runtime **already bridges**
      them with `sprout_utf8_codepoint_prefix_count` (`:5522`). Correct, and now pinned by tests that
      assert the exact codepoint offset instead of merely "it worked".
    - `url` is the model the others should follow: it operates on `Bytes` throughout and decodes UTF-8
      exactly once at the end (`bytes.to_string`), which its header comment calls "the only correct
      level". It already had non-ASCII coverage including a byte-length assertion.
  - **Coverage added** (the durable half — the bugs are fixed, but only tests stop the axis reopening):
    `test_regex.spr` +8 (it had **zero**, and one asserts a match offset as a *number*, 5 not 6, so a
    byte/codepoint regression is a wrong integer rather than a silent pass); `test_template.spr` +5 on
    the **data** side, since the one pre-existing check put é in the template *source* while the
    realistic case is user text rendered into HTML; `test_http_server_parse.spr` +2 for the request
    line, including a multi-byte PATH with a multi-byte BODY — the sharpest test of the body-offset
    derivation, because `/日本` is 3 codepoints and 7 bytes, so the offset is only right if derived as
    `byte_length(take(raw, body_start))` rather than assuming `body_start` is already a byte count.
  - **Method, for re-running it:** `for f in tests/stdlib/*.spr; do sed 's/#.*//' "$f" | LC_ALL=C grep
    -c '[^ -~\t]'; done` — stripping comments first is essential, since em dashes and `✓` in prose
    make a naive grep match nearly every file and report ~260 suites as covered when only 17 have
    non-ASCII test *data*.
- [x] `P3` **`term_read_key` panicked on a non-ASCII keypress. FIXED 2026-08-11.** It now assembles
  the continuation bytes into a complete character and substitutes **U+FFFD** when the sequence is
  truncated or invalid, instead of aborting. Typing an accented or non-Latin character in the REPL no
  longer kills the session.
  - **Why U+FFFD rather than an error channel** — the opposite of the choice made for
    `bytes.to_string` / `read_file` / the http_server body, all of which reject: those return a
    `Result` to a caller that can act on it, and `term_read_key` returns a bare `String` to a REPL
    with no meaningful recovery for "that keypress was malformed". Verified prior art: the **WHATWG
    Encoding Standard** ("The constraints in the UTF-8 decoder above match 'Best Practices for Using
    U+FFFD' from the Unicode standard"; "No other behavior is permitted per the Encoding Standard")
    and **Rust's `String::from_utf8_lossy`** ("will replace any invalid UTF-8 sequences with `U+FFFD
    REPLACEMENT CHARACTER`").
  - The invariant the abort protected — every returned `String` is valid UTF-8 — still holds, now by
    construction: assembled bytes go through the existing `utf8_validate`, which stays the single
    authority (catching overlongs, surrogates and out-of-range, which a length check alone would not).
    C0/C1 and F5..FF are rejected before assembly so their "maximal subpart" is the single bad byte
    and a following good byte is not dragged into the error.
  - Two details easy to get wrong, both pinned by tests: assembly must happen **inside raw mode**,
    because by the time the token dispatch runs `tcsetattr` has restored canonical mode where `read()`
    blocks for a newline (VMIN=0/VTIME=1 bounds the wait, as the arrow-key path already did); and an
    invalid continuation byte must be **pushed back**, since it is the next keystroke — swallowing it
    loses a key, which no test of the malformed key alone would catch.
  - Tests: `tests/c_runtime/term_read_key_safety.c` gains `multibyte` (2-byte) and `wide` (3- then
    4-byte, so the continuation count comes from the lead byte), rewrites `badbyte` from "must abort"
    to "must return U+FFFD", and adds `badcont` asserting the following byte survives. All confirmed to
    fail against `origin/master`'s runtime.
- [x] `P1` **W2 R2, the `net` half: socket reads mint Strings that violate the `String` invariant.
  FIXED 2026-08-11.**
  `docs/spec-v0.md` §"A `String` value is always valid UTF-8 and contains no NUL byte. Builtins that
  construct a `String` from raw external bytes (e.g. `read_file`) validate the input and report
  malformed content through their error channel rather than producing an invalid `String`."
  `tcp_read` / `tcp_read_avail` / `tcp_read_avail_timeout` do not: they wrap the recv buffer with
  `sprout_gc_adopt_cstr(buf, n, …)`, which copies an explicit `n` that may contain interior NULs. That
  one call is the whole defect — the shared `tcp_recv_avail` core already returns an explicit byte
  count, so the String-ness lives entirely in the wrapper.
  **This is not a new finding.** It is W2 R2 from
  `docs/fundamentals-code-review-handoff-2026-07-03.md` (which names `tcp_read` explicitly, "also
  silently truncates at embedded NUL", alongside `proc_run`, `term_read_line`, `env_get`, `argv_get`,
  `stdin_read_bytes`), it is listed under "Remaining unblocked correctness work" in the fundamentals
  entry below, and decision **D4** already chose the fix on 2026-07-04: *"reject invalid UTF-8,
  Bytes-primary via the `bytes_to_utf8` choke point."* It was simply never executed for `net`.
  **Verified repro requiring no HTTP at all** (`proc_run`, the sibling R2 site):
  `proc_run(["printf", "a\\000b"])` yields a String reporting `string.byte_length` **3**,
  `bytes.length(bytes.from_string(…))` **1** and `string.length` **1** — three disagreeing lengths on
  one value — and under `SPROUT_GC_HDRCHECK=1` it aborts: `HDRCHECK: str_byte_len aux=3 strlen=1`.
  **It is live in CI now:** PR #66 (`Bytes` request bodies) is red on exactly this, because sending a
  binary body routes those bytes through a String. Note the local/CI asymmetry that hid it —
  HDRCHECK is **off by default and on in CI**, so a green `just test` locally proves nothing about
  string-representation changes.
  **Fixed by executing D4 for `net`.** Added `tcp_read_some` (one recv, never parks, returns `Bytes`,
  `Err TcpWouldBlock` / `Err TcpEndOfStream`); deleted `tcp_read`, `tcp_read_avail`,
  `tcp_read_avail_timeout` and — since it called `tcp_read` — `tcp_echo_serve`, an entire echo server
  written in C, now `examples/tcp_echo_server.sprout`. Net **-4 builtins**. Deleting rather than adding
  was the point: no builtin returns a `String` from a socket, so the invalid state is unconstructible
  from I/O. No validation was added anywhere — `bytes.to_string` was already the choke point and was
  verified to reject a NUL as well as invalid UTF-8.
  **Deviations from the plan sketched here, and why:**
  * `read_exact` / `read_exact_utf8` were **left alone**. They return `Bytes` already, so they are not
    W2 R2 violators; rebuilding them over `tcp_wait` is a separate change (it would give `read_exact`
    the deadline it lacks, which is an API change) and is left to the `BACKLOG:483` entry above.
  * `read_avail` (unbounded) was **deleted** rather than rebuilt. `tcp_wait` takes a finite `ms`, so an
    unbounded park would have needed a new sentinel encoding — and an unbounded socket read is the
    hazard this series exists to remove. All three callers took a deadline without difficulty.
  * `tcp_write_string` was **kept**. Its own retirement condition is "go when the raw `tcp_*` externs
    go, not before, so the raw-handle path stays complete rather than half-usable", and the raw family
    survives (see below), so retiring it alone would have violated the condition it states.
  * `tcp_read_some` is **exported**, unlike its write-side twin, because it is now the raw family's
    only read. That family cannot retire while a force-dropped task cannot run a linear `close` —
    which is the `P2` full-duplex/cancellation gap below, now the single thing blocking it.
  **Also required, and larger than the read swap itself:** `http_server`'s header loop had a `String`
  accumulator purely to locate `"\r\n\r\n"` with `str_find`. It is gone, replaced by a new pure-Sprout
  `bytes.find` scanning a 3-byte overlap plus each new chunk — the terminator is 4 bytes, so a
  straddling occurrence has 1-3 bytes on the old side. The overlap is not an optimisation: rescanning
  the accumulated block per round is quadratic and *reachable on purpose*, since the size cap bounds
  total bytes rather than rounds (one byte per round under a 64 KiB cap forces 65536 scans of up to
  64 KiB). The header block is now decoded exactly once through `bytes.to_string`, so a NUL or bad
  UTF-8 in headers is a clean 400 rather than an illegal `String`, and every byte/codepoint conversion
  in the read path is deleted rather than commented.
  **Regression:** `tests/task_io_smoke/tcp_nul_payload.spr` pins it at the `net` layer (bytes intact
  AND decoding refused), run under `SPROUT_GC_HDRCHECK=1` as well as the default — a default-build
  green run is exactly what let this reach CI. `tests/stdlib/test_bytes_find.spr` covers the search.
  **`proc_run` DONE 2026-08-11.** Same one-line shape (`sprout_gc_adopt_cstr` on unvalidated bytes)
  and the same fix: `ProcResult Int Bytes Bytes`, decoded through `bytes.to_string`. Both defects
  measured before the change — `printf 'a\000b'` gave `byte_length 3, length 1` (silent truncation)
  and aborted under `SPROUT_GC_HDRCHECK=1` which CI has ON; `printf 'a\377b'` exited 1 with
  `builtin str_utf8: invalid UTF-8 lead byte` **when a walker touched the value, not at ingestion**,
  so binary subprocess output was unusable and died at an arbitrary later point. API: `proc_stdout` /
  `proc_stderr` are DELETED rather than retyped, so all 25 call sites had to be revisited instead of
  silently changing meaning; replaced by `proc_stdout_bytes` / `proc_stderr_bytes` (lossless),
  `proc_stdout_text` / `proc_stderr_text` (`Result Utf8Error String`, the choke point), and
  `proc_stdout_or` / `proc_stderr_or` (fallback named at the call site — deliberately not a lossy
  U+FFFD decoder, so a substitution stays visible where it happens). Regression:
  `tests/stdlib/test_process_bytes.spr`, 21 assertions covering NUL and invalid-UTF-8 on both stdout
  and stderr, each asserting bytes-intact AND decode-refused, plus valid text still decoding.
  **One real reachability this closed, beyond the filed one:** `analysis_service_driver`'s
  `collect_eval_output` fed the *evaluated program's* stdout — user-controlled — straight into a
  String, so an expression printing a NUL or a bad sequence took down the whole analysis service.
  **Ownership note for the runtime:** `bytes_from_chunk_bytes` COPIES, where `sprout_gc_adopt_cstr`
  adopted, so `sprout_make_proc_result` now `free()`s both GrowBufs. It is the single hand-off point
  for every early-return path in `sprout_proc_run_impl`, so the free belongs there and nowhere else.
  **Remaining R2 producers:** `term_read_line`, `env_get`, `argv_get`. Note none of them has a cheap
  NUL repro any more — the OS NUL-delimits env and argv so the byte cannot arrive, and
  `term_read_line` truncates at the NUL on read (measured `byte_length 1` for `a\0b\n`): data loss,
  not an inconsistent header. They can still mint **invalid-UTF-8** Strings, which HDRCHECK is blind
  to (it compares `aux` against `strlen`, and a bad lead byte leaves those equal). See
  docs/debugging.md, which now records this asymmetry.
- [ ] `P2` **Full-duplex on one socket is inexpressible, and the restriction is CONSERVATIVE rather
  than fundamental.** Verified 2026-08-11 while deciding whether `net`'s raw-handle "escape hatch" was
  worth keeping. **Promoted in consequence 2026-08-11:** this is now the sole blocker on retiring the
  raw-handle `tcp_*` family (and with it `tcp_write_string`), because a task force-dropped by
  `scope_cancel`/`with_timeout` never runs its linear `close` — so `cancel_io_drop.spr`,
  `timeout_io_drop.spr` and `await_dropped_fails.spr` cannot hold a `TcpConnection` at all, and
  `tcp_read_some` had to be exported to keep that path from being able to accept and write but not
  read. Moving a connection into ONE spawned task compiles; two tasks borrowing it
  concurrently (one reading, one writing — a proxy, WebSocket, or HTTP/2) is rejected:
  *"borrowed value 'conn' cannot be captured by a closure passed to a `once` parameter: the closure may
  run after the value is consumed. Move it instead."*
  **But the stated reason does not hold for this case.** `stdlib/task.sprout` `with_scope` "blocks
  until all tasks spawned into it finish… the join is **unconditional**", so the owner regains access —
  and therefore can only consume — after every borrower has finished. Structured concurrency already
  enforces the exact lifetime discipline that would make the borrow sound; the checker just does not
  model scope-bounded borrow lifetimes. The rule is right in general (a `once` closure could be stored
  and run later) and wrong for `task_spawn` inside `with_scope` specifically.
  **Two candidate fixes:** teach the checker that a borrow captured by a `task_spawn` closure is
  bounded by the scope's join; or add a `split` returning two linear halves with disjoint operations
  (Rust's `TcpStream::split`, and the more explicit option).
  **Not solved by a raw-`Int` escape hatch** — that discards consume-exactly-once instead of splitting
  it, so both halves could `close` or neither would. That is why `tcp_write_string` is deleted rather
  than kept as gap cover: it has zero callers, the shape it names is circular (the only source of a raw
  `Int` is `tcp_connection_handle` on a connection already held linearly), and its own comment says
  "retire it with them, not before" — which the R2 reduction above triggers.
- [x] `P3` **`just b1-gate` is RED on master, but it is a STALE FIXTURE, not a regression. FIXED
  2026-08-11.** Fixture rewritten as `vector_get_direct(v, _)`; gate now green and wired into
  `ci-fast-gates` + `gate`, so it cannot go stale unnoticed again. Three things the fix turned up that
  the original entry did not anticipate:
  (a) **The assertion was weaker than the property it named** — it only checked that the fixture
  *compiles*, which would also pass if the call were silently fully applied or erased. It now asserts a
  placeholder closure was actually built (`define … @__sprout_ir_lambda_N(i64 %env$, i64 %__sprout_ph_0)`).
  (b) **B1 *does* fire here, and correctly** — so do not "fix" this by asserting it does not. The
  closure body bounds-checks (`icmp uge` → panic) and then inlines the load indexed by
  `%__sprout_ph_0`, the placeholder bound as the closure's own parameter: the inline happens at call
  time, inside the closure. It is safe because a `Vector Double` element is a scalar, so finding ①'s
  unrooted-heap-pointer hazard does not apply. What ② forbids is the arity hard-Err, not the inline.
  (c) **`scripts/b1_gate.sh` hardcoded `-framework Security -framework CoreFoundation`**, so wiring it
  into CI as-is would have failed to link on the Linux runner. Now conditional on `uname`, the same way
  `tests/c_runtime/run.sh` already did it. It also hardcoded `build/compile_driver_bin_stage1`, so it
  could not run under `just linux-run b1-gate` (which overrides `build_dir`); it now honours
  `SPROUT_STAGE1`, and both gates were verified green **on Linux** before the wiring was pushed.
- [ ] ~~`P3` `just b1-gate` is RED~~ — original report, retained for the diagnosis: Verified
  2026-08-11: `tests/b1_fixtures/fixture_b1_partial.spr` writes an under-applied call as
  `vector_get_direct(v)`, which the language now rejects — *"expects 2 arguments, got 1 (Sprout is
  n-ary; use `_` for partial application)"*. The property finding ② guards still HOLDS: rewritten as
  `vector_get_direct(v, _)` it compiles and emits no `vec_get_d`, i.e. it reaches partial application
  and B1 correctly does not fire. Fix is the one-line fixture update. The fixture predates the
  explicit-`_` syntax and nothing caught the drift because this recipe is unreferenced (see below).
- [x] `P2` **`just c-runtime-test` is an ORPHAN GATE and had rotted completely. CLOSED 2026-08-11** —
  the remaining work (wiring it in so it cannot rot again) is done, together with the `gate-audit`
  Assertion D this entry asked for. `c-runtime-test` and `b1-gate` are now members of
  `ci-fast-gates`' GATES array and of `gate`. Assertion D inverts the audit's direction: A, B and C all
  start from something that already runs, so none could see a recipe nothing runs — D starts from the
  recipe LIST and fails on any recipe whose name claims verification (`-check`, `-gate`, `-smoke`,
  `test-*`, `verify-*`, `-audit`, `-diff`) that is unreachable from `gate` or CI, with a
  whole-line-matched exclusion list carrying a stated reason per entry (batteries, single-file
  developer entry points, `check-iface-all`'s missing precondition, `test-stdlib-stage2`, and
  `linux-smoke` which needs a container runtime CI does not need). **Discrimination verified in both
  directions**, not just green: a throwaway `probe-nothing-check` recipe was flagged, then removed and
  the audit re-confirmed green. Note the exclusion list uses `grep -qxF`, because the pre-existing
  `grep -qw` idiom would match `test` inside `test-file` and silently excuse it.
  **One more silent degradation found while wiring it in:** two of the ten cases build with
  `-fsanitize=address,undefined`, and `run.sh` falls back to an *unsanitized* build and still passes
  green when that fails to link — which it did, because `clang` on Ubuntu does not pull in
  `libclang-rt-dev`. So those two assertions would have been silently unsanitized in CI from the moment
  this gate landed there. `libclang-rt-dev` added to CI's apt line and to the `linux-smoke` image;
  verified the fallback message is gone. Related open work: the broader ASan/UBSan-suite item below is
  unaffected — this only restores the sanitizer for the ten C-level cases.
- [ ] ~~`P2` `just c-runtime-test` is an ORPHAN GATE~~ — original report, retained for the method and
  the recipe audit it records: Found 2026-08-11
  while fixing `term_read_key`. The harness's `compile()` linked only `runtime/sprout_runtime.c`; when
  the runtime was split into `sprout_scheduler.c` + `sprout_poll.c`, **every** case in
  `tests/c_runtime/` stopped linking (`symbol(s) not found: _scheduler_park_on_fd` …) and nothing
  noticed, because `c-runtime-test` is referenced by **no aggregate recipe and no CI job** — not
  `test`, not `gate`, not `.github/workflows/ci.yml`. Ten C-level regression assertions were silently
  unrunnable, including the one documenting `term_read_key`'s abort contract: that contract was
  "guarded" by a test which could not compile. The link line is fixed here (`runtime/*.c`) and all ten
  cases pass. **Remaining, and it is the actual fix: wire it into a gate so it cannot rot again** — the
  rot was caused by nothing running it, not by the link line.
  - **The audit of other unreferenced recipes is DONE (2026-08-11); results below, so this is no longer
    an unknown.** Measured: **42 of 75** recipes are reachable from neither `gate` nor CI. Most are
    legitimately manual (`repl`, `run`, `build-*`, `test-file`, `llvm-where`, `gc-profile`). The seven
    whose names claim verification were each executed:
    | Recipe | Result |
    |---|---|
    | `test-freelist-verify`, `gc-arena-check`, `gc-adapt-check`, `gc-ageprof-check` | green |
    | `check-iface-all` | needs `just refresh-iface` first — a precondition, not rot |
    | `b1-gate` | **red**, but a stale fixture, not a regression (own entry above) |
    | `c-runtime-test` | was 100% broken; fixed |
    So no live regressions were hiding behind them — 2 of 7 broken, both benign once diagnosed.
  - **`gate-audit` cannot catch this class, and that is the real gap.** It passes green today. Assertion
    A checks CI → `gate` (so a recipe absent from CI is never examined); Assertion B checks that each
    `scripts/*.sh` is *mentioned* in the justfile. `tests/c_runtime/run.sh` is not under `scripts/`, so B
    never scanned it. Worse, B validates only one hop: `scripts/b1_gate.sh` counts as "reachable"
    because the `b1-gate` recipe names it — while nothing runs that recipe. A script named `*_gate.sh`
    was rotting behind an assertion whose own comment says scripts "rot while looking like coverage".
    **Needs an Assertion C:** every recipe that claims to verify something must be reachable from
    `gate` or CI, with an explicit justified exclusion list — the same discipline B already applies to
    scripts, where adding a name "is a decision, not a formality". Caveats: wiring the six in may turn
    `gate` red, and `gate` is already ~15–25 min, so some belong in an explicit manual list instead.
  A gate nobody runs is worse than no gate, because it
  reads as coverage.
- [ ] ~~`P3` `term_read_key` panics~~ — original report, retained for the rationale:
  `runtime/sprout_runtime.c:3278`:
  a single `read()` byte `>= 0x80` is at most the lead of a multi-byte sequence, so returning it would
  mint an invalid `String`; the builtin calls `tcp_fail` instead. That is a *deliberate* choice with a
  written rationale (review W2/R4, "assembling a full multibyte key is a separate deferred feature"),
  but the rationale lived only in a C comment and the gap was recorded nowhere — filed now so it is not
  rediscovered. Consequence: typing any accented or non-Latin character in the REPL kills the session
  and loses its state. Fix is to assemble the continuation bytes into a complete codepoint before
  returning (the byte count is determined by the lead byte). Local/interactive only — no remote
  reachability, hence `P3` rather than the `P1` the `json` sibling warranted. Found 2026-08-10 by the
  text-indexing sweep above.
- [ ] ~~`P1` Sweep every text-indexing module~~ — original scoping notes, retained for the method:
  C5 and the `json.parse` abort above are two symptoms of ONE missing test axis, and there is a third on record:
  the bundler's `strip_headers_b` had the identical confusion ("byte offset was used as codepoint
  index in `str_slice`, causing parse failures on files with multi-byte characters in comment
  headers"). Byte and codepoint index spaces AGREE on ASCII, and the suites are all-ASCII —
  `tests/stdlib/test_json.spr` has **zero** non-ASCII bytes across 133 lines — so an all-ASCII suite
  cannot distinguish a byte-correct parser from a codepoint-confused one. **Method:** `LC_ALL=C grep
  '[^ -~]' tests/stdlib/*.spr` to find suites with no non-ASCII coverage; then per module audit (a)
  `string.length` compared against or mixed with a byte count, and (b) calls to text builtins that
  are process-fatal rather than `Result`-returning (`str_slice_bytes`). **Targets:** `url`,
  `template`, `regex`, `http`, `scram`, `string` itself, and `stdlib/compiler/lexer.sprout:11` (the
  third `str_slice_bytes` caller — its offsets come from the tokenizer and are *probably*
  boundary-aligned, which is not the same as verified). Add a non-ASCII case to each suite that lacks
  one. Likely multi-PR.
- [x] `P2` **HTTP request bodies are now `Bytes`, not `String`. DONE 2026-08-11.** `request_body_bytes`
  returns the body byte-for-byte (total); `request_body` returns `Result Utf8Error String`. Verified
  prior art: Go (`Body io.ReadCloser`), ASGI (`body` is a byte string), Jakarta Servlet
  (`getInputStream` binary / `getReader` character — and explicitly mutually exclusive, "Either this
  method or `getReader()` may be called to read the body, not both"). Sprout's body is buffered rather
  than streamed, so both accessors can coexist without Servlet's exclusion rule.
  - **Two corrections to the original entry below, both of which I wrote and both wrong.** (1) It said
    a NUL body is "truncated, not merely mangled" — true before the C5 fix, but after it the body was
    UTF-8-validated and such a request got a clean **400**, so this was a missing capability, not live
    corruption. (2) It said this unblocks "file uploads". It does not: the body stays **buffered** and
    capped by `max_body_bytes`, so what this enables is binary payloads up to that cap — form posts,
    JSON, protobuf, small images. Large uploads need streaming (see below).
  - **An accessor alone would NOT have worked, and only the runtime showed why:** `str_concat`
    (`read_remaining_body`'s accumulator) measures with `strlen`, so a body containing `0x00` was
    truncated *there*, before any accessor could see it. The representation had to change, not just the
    surface. The header phase now keeps two accumulators — a `String` purely to locate the terminator
    with the native substring search, and the authoritative bytes — because a binary body can arrive in
    the same TCP segment as the headers.
  - **Root cause fixed in the runtime:** `bytes_from_utf8` measured with `strlen` while `str_byte_len`
    reads the CSTR header, so the two disagreed on any String with an interior NUL — measured, a String
    holding `"a\0b"` gave `byte_length` 3 and `bytes.length(from_string(…))` 1, silently dropping
    payload. It now reads the header, restoring
    `bytes.length(bytes.from_string(s)) == string.byte_length(s)`. Safe universally because every
    String is headered (`SPROUT_GC_HDRCHECK=1`). Side effect worth knowing: `net.write_all_utf8` no
    longer truncates a NUL-containing payload mid-send.
  - **Behaviour change:** an invalid-UTF-8 body used to be refused **400 on the read path**. It now
    parses (the bytes are exactly what `Content-Length` announced) and surfaces as `Err` from
    `request_body`, so a binary handler is no longer refused on a text assumption it never made. The
    body accumulator also moved from `bytes.append`/`str_concat` to a `Builder`: quadratic in chunk
    COUNT (16 table entries for a 1 MiB body) instead of in bytes (~8 MiB copied).
  - Tests: `tests/task_io_smoke/http_binary_body.spr` (payload `0x00 0xFF 0x41`, chosen so each old
    failure mode is separately fatal — a strlen accumulator stops at the NUL, a validator rejects the
    0xFF, and the trailing byte is missing if anything truncated; both the buffered and read-loop
    paths) and 5 checks in `test_byte_length.spr` pinning the `from_string`/`byte_length` invariant.
- [ ] `P2` **Stream request bodies instead of buffering them.** Raised 2026-08-11 when the user asked
  why Go, ASGI and Servlet all stream — the right question, and the honest answer is that buffering
  forfeits everything streaming provides: memory is O(body) not O(chunk); **chunked transfer encoding
  has no `Content-Length`, so "buffer it all" is unbounded by construction** and cannot be supported at
  all; there is no backpressure; a proxy cannot forward incrementally; and a handler cannot abort
  mid-body. The `Bytes` work above is a *prerequisite*, not a substitute — a stream yields `Bytes`
  chunks, never `String` chunks, and the `bytes_from_utf8` header fix is exactly what lets each read
  chunk become `Bytes` losslessly. What streaming replaces is the accumulator, not the element type;
  `request_body_bytes` becomes the collect-it-all convenience every streaming API also offers (Go's
  `io.ReadAll`, ASGI's `more_body` concatenation). **Design constraints:** it interacts with the
  occupancy bound, which currently assumes the handler runs AFTER the whole body is read — streaming
  makes them concurrent and changes that math; and it wants to land with or before chunked encoding,
  since that is the case buffering cannot express.
- [ ] ~~`P2` `request_body` is typed `String`~~ — original entry, retained for the survey notes and as
  a record of the two claims corrected above: Byte-exact
  framing (C5) fixes the *arithmetic* but not the *ceiling*: `bytes_from_utf8` measures with
  `strlen`, so a Sprout `String` is NUL-terminated and a body containing `0x00` is **truncated**, not
  merely mangled. File uploads, `multipart/form-data`, protobuf and image `POST`s are therefore
  structurally unrepresentable through `request_body`, and no amount of correct byte counting changes
  that. **Design decision needed** (public API change, hence its own item): does
  `request_body` return `Bytes` with a `request_body_utf8` convenience, or does a second accessor
  expose the raw bytes alongside it? Prior art to survey: Go's `http.Request.Body` is an
  `io.ReadCloser` of bytes with decoding left to the caller; Rust's `hyper` uses a `Body` of `Bytes`
  chunks. Raised 2026-08-10 while fixing C5.
- [ ] `P1` **Decode `Transfer-Encoding: chunked` request bodies.** Today every `Transfer-Encoding` is
  refused with 501 (`stdlib/http_server.sprout`, `parse_body_framing`), which is conformant — RFC 9112
  §6.1 says a server receiving a transfer coding it does not understand SHOULD respond 501 — but it is
  a real capability gap, not an exotic one: any client that cannot know its body length up front uses
  chunked (a generator body in Python `requests`, `curl -T -`, Java's
  `BodyPublishers.ofInputStream`, any Go client with unknown `ContentLength`). Before 2026-08-11 the
  header was not read at all and such a request framed as an EMPTY body, so the handler answered 200
  having silently discarded the entire payload; the 501 converts that into a loud failure, which is
  the improvement, not the fix. **Scope:** chunk-size line parsing (`<hex>[;ext]\r\n`), the read loop
  wired into `read_remaining_body`, `max_body_bytes` enforced against the RUNNING total rather than an
  announced one, the trailer section consumed, and CL+TE still refused as the smuggling shape RFC 9112
  §6.3 calls out. **Depends on** the streaming item above: chunked has no `Content-Length`, so the
  size-bounded buffer this file frames against cannot express it — which is why these two land
  together. Raised 2026-08-11 by the http-subsystem code review (finding 4).
- [x] `P1` **HTTP CLIENT response bodies are `Bytes`. RESOLVED 2026-08-12.** `HttpResponse`'s third
  field was `String`, and `http_response_result` took only a `char*` — the recv accumulator's exact
  `len` was dropped at that call boundary, so the body had to be re-measured with `dup_cstr`
  (`strlen`). A body containing `0x00` was silently truncated at it and returned as `Ok`: a 20-byte
  payload `AAAA\0BBBB\xffCCCCCCCCCC` arrived as **4 bytes**, measured live before the fix. Nothing
  validated UTF-8 on that path either, so non-text bytes were minted into a `String` unchecked —
  a direct violation of spec-v0.md's rule that a builtin constructing a `String` from external bytes
  validates and reports through its error channel. `http_decode_chunked_body` had the same defect one
  layer down (`*cursor != '\0'` bounds, `strlen(cursor) < chunk_size`), where it surfaced as a
  spurious "truncated chunk data" `HttpDecode` on a perfectly well-formed response. Now: length
  threaded through, body copied into a `BytesVal`, chunked decoder bounded by `body_len`,
  `http_response_body -> Bytes` plus `http_response_text -> Result Utf8Error String`. Mirrors the
  server-side request-body decision. Gate: `just http-client-binary-gate` (both body paths).
  Raised by the http-subsystem code review (finding 8).
- [ ] `P2` **List-valued request headers.** `parse_header_lines` folds repeats into a `Dict String`
  last-wins. The two cases where that fold is a *framing* hazard are now refused outright
  (`content-length` with differing values, any repeated `host`), but the general fold remains, and
  `Cookie` legitimately arrives as several field lines (RFC 6265) — those collapse to the last one.
  Needs an accessor returning all values for a name (Go's `Header` is `map[string][]string`; Rust's
  `HeaderMap` is multi-map) alongside the existing single-value `request_header`. Raised 2026-08-11
  by the http-subsystem code review (finding 3), which found the framing half.
- [x] `P2` **Header parsing was quadratic in a single header line's length.** *(Done 2026-08-12.)*
  The 2026-08-11 fix had retired the worst term (`split_header_lines` cuts on `\r\n` with `str_find`
  instead of the O(index)-per-read `char_at_or` walk). What remained was `lower_ascii` and
  `string.trim`, which rebuilt a `String` one codepoint at a time — Θ(n²) per header LINE, with no
  yield point, so on the single-threaded cooperative scheduler one connection's parse froze the accept
  loop and every in-flight handler. Measured at these sizes: a ~1 s freeze over a 30 000-byte name +
  30 000-space value. Fixed by rewriting both as O(n) over `Bytes` (O(1) indexed access) in
  `stdlib/string.sprout`: `trim`/`trim_left`/`trim_right` scan for the non-whitespace bounds and slice
  once; the new `string.to_lower_ascii` coalesces unchanged runs into verbatim slices and joins in one
  `string_concat_many` pass. `http_server`'s local `lower_ascii` (and its `char_at`/`lower_char`
  helpers) are deleted in favour of the shared stdlib function. Both are byte-exact on multibyte UTF-8
  (the only split points are ASCII whitespace / A..Z, always single-codepoint). No `task_yield` was
  needed — O(n) over a 64 KiB line is sub-millisecond, so the freeze is gone without a yield point.
  Regression: `tests/task_io_smoke/http_header_lower_parks.spr` (liveness, RED at 1058 ms) +
  `tests/stdlib/test_string_case_trim.spr` (correctness incl. UTF-8). Lesson worth keeping: Sprout's
  persistent `Vec`/`Builder` appends are all full-copy, so a byte-by-byte fold through EITHER is still
  Θ(n²) — a genuinely O(n) pure build needs `string_concat_many` (bulk join) or divide-and-conquer,
  not an accumulator. Raised 2026-08-11 by the http-subsystem code review (finding 5).
- [ ] `P3` **`bytes_slice`'s extern declaration has misleading parameter names.**
  `stdlib/prelude.sprout` and `stdlib/bytes.sprout` declare `bytes_slice(b: Bytes, from: Int, to: Int)`,
  but the C implementation takes `(start, count)` and clamps `count` — `bytes.slice`'s wrapper gets it
  right (`start, count`) while the extern it calls is named as if it were a half-open range. Anyone
  reading the extern will compute `to` and silently get a shorter slice than intended. Rename the
  extern's parameters to `start`/`count`. Trivial, but it touches the prelude, so it needs the
  seed-refresh path rather than riding along with an unrelated change. Noticed 2026-08-10 while
  fixing C5 (I nearly mis-called the semantics from the declaration alone).
- [ ] `P2` **Task-boundary panic isolation ("let it crash" at task granularity).** Today a panic in any green task — e.g. an http_server handler raising or hitting a bug — reaches `runtime`'s process-fatal path (`tcp_fail`/`exit(1)`/`_exit(127)`), so one bad request kills the whole server. Give the scheduler a per-task panic boundary: a panic unwinds to the spawning `with_scope`/`task_spawn` frame, marks that task failed, reclaims it, and lets siblings continue; a supervisor (the accept loop) observes the failure and responds (500 + loud log) instead of dying. This also fills the gap `task.sprout` flags ("no siblings auto-cancelled on failure yet; Sprout has no exceptions to inject"). It is the runtime half of an abortive `Exn` effect handler at task granularity, buildable without any type-system work, and it de-risks the eventual `Exn`-effect unwinding (see `docs/effect-system-handlers-draft.md` and `docs/math-partiality-v0.md` §4). **Caveats to design around:** (1) GC type-aware rooting must be settled on unwound frames or the heap corrupts (`docs/compiler-internals.md`); (2) green tasks share the GC heap/channels, so a task that panics mid-mutation of shared state can leave it inconsistent — the safe contract is *terminate + notify supervisor*, never resume-in-place, and steer shared state toward message-passing. Distinct from C3 (that item is `tcp_accept`/EMFILE isolation; this is *handler-body* panic isolation). Raised 2026-07-30 during the math-partiality design discussion.
- [x] `P2` **`tcp_echo_serve` is a serial accept→read→write→close loop (concurrency review C4).
  RESOLVED 2026-08-11.** The builtin is **deleted** — an entire echo server implemented in C, which
  AGENTS.md "Builtin vs Stdlib" put in Sprout's half all along. It went not by choice but by
  dependency: it called `tcp_read`, and reimplementing its park/retry in C would have re-added exactly
  what the W2 R2 fix removed. `examples/tcp_echo_server.sprout` is now a Sprout accept loop over the
  linear `TcpListener`/`TcpConnection` API. Taken with the "document it as intentionally serial"
  option this entry offered: it stays one-connection-at-a-time and says so, since
  `stdlib.http_server` (`serve`, `serve_pooled`) is where the concurrent shapes belong and a minimal
  illustration should not duplicate them. Two things improved beyond the head-of-line question the
  entry was about: the loop now has a **read deadline** (the C version had none, so one silent client
  parked it forever — strictly worse than the head-of-line blocking this filed), and a socket error
  drops that connection instead of aborting the process.
- [ ] `P2` **Template engine phase 2 — filter pipeline + `loop.index`.** Follow-up to the phase-1 Jinja-flavored HTML template engine (`stdlib/template.sprout`, PR #203, built on top of the http_server routing/render work). Add a filter pipeline (`upper`/`lower`/`default`/`length`; the parser already special-cases `{{ path | safe }}`, so generalizing to a `{{ path | filter }}` chain is the natural next step), `loop.index` inside `{% for %}` bodies, and a web-server example that renders an HTML page from a template beyond the existing `/users` route. Design doc + full roadmap: `docs/template-engine-v0.md`.
  - [ ] `P3` **Template engine phase 3 — inheritance/includes.** `{% extends %}`/`{% block %}`/`{% include %}`-style template composition, if demand warrants once phase 2 ships. Design doc: `docs/template-engine-v0.md`.
- [x] `P2` **`stdlib/log` — leveled, structured logging. LANDED (two stacked PRs).** Design of record: `docs/logging-v0.md`. PR #213: builtin `wall_time_micros() -> Int !{IO}` (exposes `gettimeofday`; correctness-justified in `APPROVED_BUILTINS`) + `stdlib/log.sprout` (levels `Debug|Info|Warn|Error`, threaded `Logger` capability value with a swappable `String -> Unit !{IO}` sink, immutable `with_field`, ISO-8601 UTC timestamps via pure-Sprout `format_iso8601`). Follow-up PR: `response_status` accessor on `stdlib/http_server.sprout` + `stdlib/http_middleware.sprout` (`with_logging` access-log middleware, graduated to a module for testability) + wired into `examples/http_web_server.sprout` (logger threaded as an explicit capability; startup `print` → `log.error`). Aligns with `docs/observability-guard-rails.md` #3 (log sink passed explicitly, not global). Tests: `test_log.spr`, `test_time_iso8601.spr`, `test_http_middleware.spr`.
  - [ ] `P3` **`stdlib/log` follow-ups (deferred from v0):** (a) escape/quote field values containing spaces or `=` in the line formatter; (b) a JSON-lines formatter variant (the sink/format split already makes this a new formatter, not an API change); (c) graduate `format_iso8601` + a `wall_now_micros` builtin re-export into a dedicated `stdlib/time.sprout` once a second consumer appears. See `docs/logging-v0.md` §9–§10.

### 2.5) Binary Data and Protocol Primitives

- [x] `P0` Add a stable `Bytes` type for raw binary data handling.
- [x] `P0` Add byte primitives: length, indexing, slicing, append, and construction.
- [x] `P0` Add big-endian integer encode/decode helpers for framed protocols.
- [x] `P1` Add UTF-8 string/bytes conversion helpers and null-terminated string helpers.
- [x] `P1` Add efficient byte-buffer/builder utilities so protocol parsers do not depend on repeated full-copy concatenation.
- [ ] `P2` Fix `bytes_builder_append` to O(1) amortized: currently O(n_left + n_right) per call (copies full chunk arrays into a new flat array), so a `list_fold` over n strings costs O(n²) total chunk copies. Switch to a tree/rope representation where append creates a new internal node pointing at both operands (O(1)), and `builder_build` traverses the tree once (O(total_bytes)). Also add `builder_str(s: String) -> Builder` (wrap a string as a single-chunk builder without a Bytes intermediary) and `builder_to_str(b: Builder) -> String` (build + return String directly, skipping the Bytes allocation + UTF-8 round-trip). These three changes unblock a pure-Sprout `string_join_suffix` implementation using `list_fold` + builder.

### 3) JSON Support

- [x] `P0` Add `json_parse(String) -> Result JsonError Json`.
- [x] `P0` Add JSON query helpers (`json_get`, `json_get_string`, `json_get_int`, `json_get_array`, etc.).
- [x] `P1` Add `json_stringify(Json) -> String` for debug and payload building.
- [ ] `P2` Reimplement `json_stringify` in Sprout once string/escaping primitives make that practical, keeping host builtins reserved for impossible or efficiency-critical operations.
- [x] `P1` Add tests for malformed input and edge cases. `tests/stdlib/test_json.spr`: 5 happy-path, 8 malformed-input (all return `Err _`), 4 accessor edge cases, 1 stringify round-trip; 18/18 PASS.

### 4) Terminal UI Runtime

- [x] `P0` Add terminal builtins: alternate screen, clear, cursor move, hide/show cursor, style/color. **Audited 2026-08-15 — all five capabilities work; the `[~]` was a stale label, and the entry's framing ("builtins") is what went out of date, not the capability.** Only *clear / cursor move / hide / show cursor* are builtins (`term_clear`, `term_move`, `term_hide_cursor`, `term_show_cursor`, all in `runtime/APPROVED_BUILTINS`). **Alternate screen and style/color are deliberately NOT builtins** — `stdlib/terminal.sprout` composes them in Sprout as ANSI escapes over `term_write` (`enter_alt_screen`/`exit_alt_screen`, `set_bold`, `reset_style`, `set_fg`/`set_bg` in 24-bit truecolor). That is the outcome "Builtin vs Stdlib" rule 4 prescribes: `term_write` already sufficed, so no builtin was warranted. Verified byte-for-byte against the ANSI spec by `tests/conformance/run/terminal_escapes` (added in the same change — the module previously had **no test anywhere**; its only exercise was `examples/sentry_issue_browser_tui.sprout`).
- [~] `P0` Add key input primitive — single-key read **done**, non-blocking/poll **absent**. **Audited 2026-08-15.** `term_read_key` drives termios raw mode, decodes arrow keys / ctrl-chords / multi-byte UTF-8, and bounds continuation reads with `VMIN=0`/`VTIME=1` so a truncated escape sequence yields U+FFFD instead of hanging. What is missing is not merely a poll flag: `term_read_key` is a plain blocking `read(2)` that **does not park in the scheduler**, unlike `tcp_accept` (cf. the parking note at `runtime/sprout_runtime.c:158`), so in a multi-task program a key read holds the scheduler thread rather than yielding like every other blocking operation in the language. The only `poll` loop in the runtime is the process-spawn one; there is no stdin readiness path. Today's workaround is a blocking event loop (what `examples/sentry_issue_browser_tui.sprout` does), which is fine for purely input-driven TUIs and fails as soon as a UI wants timers, animation, or concurrent network alongside input. **Scoping note: making stdin a parking-capable descriptor is a runtime change and needs sign-off under "Builtin vs Stdlib" rules 5–6 before implementation.** The input side is also the half of `stdlib.terminal` that the new golden-stdout test cannot cover (it depends on stdin and `isatty`).
- [ ] `P1` Add line wrapping / viewport helpers in stdlib.
- [ ] `P1` Add basic event loop utility for TUI apps.

### 5) Data Structures and Collections

- [x] `P1` Add practical indexed sequence type (`Array`/`Vector`) for UI lists.
- [x] `P1` Add dictionary/map type for API payload handling.
- [x] `P1` Add stdlib text parsing helpers: `string_lines(String) -> Vec String`.
- [x] `P1` Add stdlib digit helpers: `string_digits(String) -> Vec Int`.
- [ ] `P2` Generalize `string_join_newlines` C workaround to `string_join_suffix(suffix: String, lines: List String) -> String` (drop the hardcoded `\n`), then re-implement it in pure Sprout using `list_fold` + builder once the builder O(1) fix and `builder_str`/`builder_to_str` primitives land (see section 2.5). Remove the C builtin once the native implementation is verified equivalent. `string_join_newlines` was introduced as a workaround (2026-05-11) to eliminate a 204K-deep recursive right-fold in `codegen.sprout`'s `join_line_sections` that caused a 2.6 GB memory spike during stage-2 self-compile.
- [x] `P2` Extend collections helpers (`vec_slice`, `vec_reverse`, `dict_keys`, `dict_values`).
- [~] `P2` Add vector utility combinators (for example `vec_sum_by`; `vec_max_subsequence_by_count` is now a maybe/later item).
- [x] `P2` Native `vector_concat(a: Vector x, b: Vector x) -> Vector x` runtime primitive to back `Vec`'s `Semigroup` append. **DONE 2026-07-12.** `instance Semigroup (Vec a)` now unwraps both `Vec` operands and calls the `vector_concat` builtin (single `n+m` backing array, two `memcpy`, zero cons cells). Measured on 20k appends of two 1000-element `Vec Int`: the old list round-trip (`vec_from_list(list_append(vec_to_list l, vec_to_list r))`) ran ~2060 ms; `vector_concat` runs ~51 ms — **~40× faster**. The gap is GC churn: the round-trip allocated ~3000 tiny cons cells per append (40,002 collections total), the builtin allocates 1 managed object per append (11 collections). A pure fused-list variant (build right's list then prepend left onto it, skipping the `list_append` copy) was measured at ~1.45× and rejected in favour of the builtin. Builtin justified per Builtin-rule #6 (concrete measured bottleneck); listed in `runtime/APPROVED_BUILTINS`. Regression coverage: element-level order + both-side empty-identity assertions in `tests/stdlib/test_collections.spr` (the prior check only asserted the sum). Follow-up left open: codegen could peephole `vec ++ vec` straight to `vector_concat` (like `String`/`List`).
- [x] `P2` Add set type and common ops.
- [x] `P2` Extend `deriving (Ord)` to support field-bearing constructors. Shipped on `feat/deriving-v1-loose-ends`: uses inline nested-match lexicographic chain (`match compare(l0, r0) with | 0 -> ... | c -> c`); no prelude additions needed; nullary ctors still work unchanged.
- [ ] `P2` Design format-agnostic serialization (serde-style Serializer/Deserializer visitor split). The initial `Serialize`/`Deserialize` classes shipped on `feat/deriving` were reverted 2026-06-10 because they conflated polymorphism with format choice: the class names suggested format-agnostic dispatch, but the implementation hardcoded an S-expression wire format. Target design (post-Sprout-1.0): user-facing `Serialize a` class with a single method that takes a format-specific `Serializer` argument (and corresponding `Deserialize` taking a `Deserializer`); format implementations (S-expression, JSON, MessagePack, future protobuf) implement the visitor traits. Mirrors `serde` in Rust. Prerequisites: design `Serializer`/`Deserializer` typeclasses (likely needs higher-kinded dispatch or rank-2 trait methods), decide on stream vs tree intermediate (aeson-style two-stage or serde-style direct events), pick error-accumulation strategy. Defer until a concrete use case beyond iface materializes; until then iface hand-writes per-type codecs (`encode_decl`, `decode_decl`, etc.).
- [x] `P2` Add `instance Eq Char` to `stdlib/prelude.sprout`. Shipped on `feat/deriving-v1-loose-ends`: uses `left == right` (lowers to `str_eq` via `compare_needs_ptr_dispatch`); also added `instance ToString Char` using `char_to_string`.
- [x] `P2` Fix constrained-function calls that dispatch only on return type (surfaced 2026-06-10, fixed 2026-06-12). **Root cause**: `inject_constrained_fn_dicts` runs before body-vs-return-type unification (`s2`), so when the constraint's type variable appears only in the return position the dict is never injected. **Fix (2026-06-12)**: added `maybe_rewrite_constrained_fn_call` + `find_inst_in_return_type` post-pass in `infer.sprout` — mirrors the existing `resolve_dispatch_typed_expr` post-pass for class methods. For each `@constrained_N` marker with a missing dict, scans the concrete return type for a matching `@inst` entry. Skips still-polymorphic call sites (returns nothing, lowering's @fwd path handles them). **Known limitation**: `fn f(x: b) -> a where Foo a, Eq b` — if constraint 0 (Foo a, return-position) fails at inference time while constraint 1 (Eq b, input-position) succeeds, the existing-dicts list has only one entry with the Eq dict; the post-pass positionally maps that Eq dict to constraint 0 (Foo) — wrong. This mixed return-first / input-later ordering is not yet handled. Workaround: declare input-position constraints before return-position ones in the `where` clause. Tracked for follow-up. **Reproducer (now passing)**: `tests/stdlib/compiler/test_constrained_fn_return_type_dispatch.spr`. **UPDATE 2026-07-19 (§13.3(B) S3):** a related mixed-constraint miscompile is now FIXED — a *concrete-ctor* constraint (`where Label a, Foldable List`) silently dropped its dict when a non-container arg preceded the container, because `resolve_obligation` fell to `first_concrete_arg(guess)` and grabbed the wrong arg (order-dependent: `Foldable List, Label a` worked, `Label a, Foldable List` didn't). Now that the callee's Scheme carries the complete `(head_token, class)` constraint list, `inject_constrained_fn_dicts_via_field` resolves a concrete-ctor head DIRECTLY via `@inst:{class}:{head}` (order-independent); the resolved dict makes `existing_dicts` dense so the post-pass mapping is correct too. Byte-identical on 241/242 corpus files (only the new reproducer's IR changes). Reproducer: `tests/stdlib/test_mixed_constraint_dispatch.spr`. The return-position *tyvar* positional-mapping case above is orthogonal (a tyvar head, not a concrete ctor) and remains as originally described — now filed as its own item directly below.
- [x] `P2` **Return-first / input-later constraint ordering mis-maps hidden dicts — FIXED 2026-07-19** (stacked on §13.3(B), item 4). When a constrained fn declared a RETURN-position constraint before an INPUT-position one — e.g. `fn pick(x: b) -> a where Default a, Tag b` (Default's var `a` is return-only) — `Default a` was UNRESOLVED at inference (no dict injected), so `existing_dicts` held only `Tag`'s dict. The return-position post-pass `build_constrained_fn_dicts_via_field` (infer.sprout) then indexed `existing_dicts` **positionally** and handed `Tag`'s dict to `Default`'s slot. On the current compiler the `verify_dispatch` Core-lint pass CATCHES this as a located compile error (`dictionary for 'Default a' resolved to 'Default Bool' but the call fixes a = Int`) rather than miscompiling silently — so valid code was rejected (pre-verifier it was a silent miscompile). Workaround was to declare input-position constraints first. **Fix (enabled by item 4's complete, class-labeled field):** the post-pass now matches each already-injected dict to its constraint **by class** (short-name compare, robust to bundling qualification) via `take_dict_for_class`, filling still-missing slots from the concrete return type. Positional indexing (`list_nth_maybe_tdict`) deleted. Byte-identical except the files exercising the ordering. Regression test `tests/stdlib/test_return_position_constraint_order.spr` (RED = dispatch-verify reject, GREEN = 42). **Residual corner (documented in-code, not fixed):** two constraints of the SAME class where one is an unresolved return-position gap cannot be told apart by class alone — no worse than the old positional behavior.
- [ ] `P1` Implement polymorphic-keyed dicts (`Dict k v` in place of the current `Dict v` keyed only on `String`). Requires: (a) new `Hash a` typeclass — pick method signature (`hash(a) -> Int` vs `hash(a) -> Bytes`) and algorithm (FNV-1a / Murmur3 / wyhash among the obvious candidates) with corresponding runtime primitives for primitive types; (b) runtime hashmap reshape — either grow `Map v` to take a callback hash, or coerce all keys through `Hash` to a canonical `Bytes` representation and reuse the existing string-keyed map; (c) migration of every `Dict ...` site in `stdlib/` and `stdlib/compiler/` (currently all string-keyed by convention) plus `Eq Dict` / `ToString Dict` / `dict_keys` / `dict_values` / `dict_entries` API updates. Justification: the current `Dict` forces callers to pre-stringify any non-`String` key, which contradicts typeclass-based design and blocks idiomatic use of structurally-keyed maps. Unblocks `deriving (Hash)` as a ~1-day follow-up emitter in the deriving codegen tool once polymorphic keys exist; `Hash` was explicitly deferred from the `deriving` v1 set on 2026-06-09 because no in-language consumer exists today.
- [ ] `P2` Improve `str_slice` perf — currently O(source_len) due to codepoint→byte conversion walking the whole source per call. Hot-loop callers (lexer, parser, future deserializers, anything that uses `str_slice` per token) silently incur quadratic cost in input size. Two viable directions: (a) add a byte-indexed `str_slice_bytes(s, byte_start, byte_len) -> String` that skips codepoint translation entirely — callers that already operate in bytes (which is most of them) opt in; codepoint-indexed `str_slice` stays for user code; (b) maintain a codepoint-to-byte index cache on String values so subsequent `str_slice` calls into the same String are O(1) per call after a one-time O(n) build. (a) is simpler and matches Rust's `&str[range]` semantics. Document the current cost (and the workaround) in `stdlib/prelude.sprout` near the existing `str_slice` declaration. Surfaced repeatedly during deriving v1 perf investigation (2026-06-09 / 2026-06-10). **UPDATE 2026-08-08 — direction (a) shipped but did not deliver until now, and the failure mode is the lesson.** `str_slice_bytes` was added as prescribed, and `str_starts_with_at_byte` alongside it, but **both opened with `strlen(s)` over the whole string** just to bounds-check the offset — so the byte-indexed replacement was itself O(source_len) per call, and every scanner that migrated to it stayed quadratic. It read as fixed everywhere that mattered: this item was marked as having a shipped direction, `str_starts_with_at_byte`'s doc comment claimed "O(|prefix|)", and `stdlib/json.sprout:48` asserted "both are O(1)/O(len) at a byte OFFSET, so one left-to-right pass". Three separate places stated the intended complexity and none of them was true, because nothing measured it. Now genuinely O(1) in `|s|` (see the landed entry under *CI / Build Performance*), guarded by a cost test rather than a comment. **Still open:** codepoint-indexed `str_slice` remains O(source_len) — that is the part of this item that is not done, and direction (b) is still the option for it.
- [ ] `P2` **Prelude O(n²) audit** (fundamentals review, 2026-07-02/03): several prelude helpers are quadratic via naive list-append recursion, mostly undocumented per the perf-docs convention (`feedback_prelude_perf_docs`). Confirmed candidates: `ToString (List a)`/`ToString (Vec a)`/`ToString (Dict v)`, `mconcat`, `list_dedup`, `Semigroup (Dict v)`, and several `vec_*` helpers (`vec_map`/`vec_filter`/`vec_filter_map`/`vec_reverse`/`vec_slice`) — `vec_sort_by`'s doc comment additionally claims a complexity it doesn't have (documents O(n log n), actually rebuilds O(n²)). Audit each, document true complexity inline (or fix to linear where the cost is cheap to remove), matching the pattern already applied to `vector_concat` (item above) and `bytes_builder_append` (§2.5). Full findings + probe files: `docs/fundamentals-code-review-handoff-2026-07-03.md`.

- [ ] `P2` **B4 — `list_length` flaky / unreliable on complex element ADTs.** Long-standing gotcha (`feedback_sprout_syntax_gotchas` memory), re-confirmed relevant 2026-07-09: `examples/digit_recognizer/recognizer.sprout` hand-writes two monomorphic length helpers (`ilen : List Int -> Int`, `dataset_size : List (MutVec Double, Int) -> Int`) purely because `list_length` (`prelude:413`) misbehaves on non-trivial element types. Root-cause and fix `list_length` so those hand-rolled helpers can be deleted; add a regression over a `List` of a field-bearing ADT / tuple element. Likely the same dispatch/monomorphization family as B2. Fixing it removes duplication from the recognizer rewrite for free.
- [x] `P2` **Vec literal lowering — `[…]` denotes a `Vec a` in a `Vec`-expected context. LANDED 2026-07-13** (`feat/vec-literal-coercion`). `[1,2,3]` desugars at parse time (`parser.sprout:865`) to `Cons` cells (`List a`), so `Vec` values previously required `vec_from_list([...])`. Now the pre-typecheck context-desugar pass (`checker.sprout`) rewrites a **syntactic list literal** (a `Cons`-headed call or bare `VarExpr("Nil")`) in a `Vec`-expected slot to `vec_from_list([…])`. Two edits: (1) `type_texpr_to_maybe_name` returns the head constructor of an *applied* type (`Vec Int` → "Vec") so a parameterized expected type activates a context — this was the non-obvious blocker (StringTemplate is nullary, so the gap never showed); (2) a `"Vec"` branch in `desugar_expr_i` (+ `ctx_is_active`/`desugar_ctx_leaf_i`/`wrap_vec_from_list`), threading through if/match arms. **Literal-only is the sound boundary, not a limitation:** a pre-typecheck pass can't tell a `List` var from a `Vec` var, and `vec_from_list(vec)` would be a type error — a `Cons`/`Nil` head is the one shape provably a `List`. Matches prior art (Haskell `OverloadedLists`, Swift `ExpressibleByArrayLiteral` affect literal syntax only; Rust declines). Covers call-arg and fn-return position. Test: `tests/stdlib/test_vec_literal_coercion.spr` (5 assertions incl. real-Vec passthrough guard). Spec clause in `spec-v0.md` §5.5. Design: `docs/coercions-and-literals-v1-draft.md` (Case A). **Deferred:** coercing a `List`-typed *variable* into a `Vec` slot (needs a post-typecheck typed-AST rewrite; never the stated pain); zero-intermediate `IsList`-style literal (§5.A).
- [ ] `P2` **Tail-spread syntax for list-literal expressions (`[a, b | tail]`).** Raised 2026-07-13 during the Cons-chain-to-list-literal refactor. List-literal **patterns** already support a tail spread (`parse_list_pattern`, `parser.sprout:370-400`: `[]`, `[a, b]`, `[a, b | rest]`), but list-literal **expressions** do not — `parse_list_literal`/`desugar_list_literal`/`collect_expr_list` (`parser.sprout:854-961`) only build a fixed, comma-separated, `Nil`-terminated chain. This leaves a real asymmetry: constructs like `Cons(h, Cons(sep, acc))` (prepending a fixed number of elements onto an existing list variable — e.g. `stdlib/string.sprout:167`, `stdlib/prelude.sprout:419/1035/1061/1076`, `stdlib/compiler/ir_rooting.sprout:347`, `stdlib/compiler/driver.sprout:184/206`) cannot be written as list literals today, unlike their `Nil`-terminated siblings which convert cleanly to `[a, b, c]`. Adding expression-side `[a, b | tail]` (desugaring to `Cons(a, Cons(b, tail))`, mirroring the existing pattern-side desugar) would close the gap and let these accumulator-building call sites read as literals too. Needs its own Design Change Process pass (prior-art survey — e.g. Haskell's lack of a literal cons-spread vs OCaml's `a :: b :: tail`, Elm's `::` operator) since it's new expression syntax, not a mechanical rewrite.
- [ ] `P2` **B5 — half-open iteration helper for the new combinators.** Surfaced 2026-07-09 (advisor review of the combinator work). The iteration combinators take an inclusive `IntRange`, so the common half-open `[0, n)` loop is `range(0, n - 1)` — which is **wrong at `n == 0`**: `range(0, -1)` flips to a descending step and iterates over `0` and `-1` instead of zero times. The hand-rolled `if i >= n` loops it replaces handled `n == 0` for free, so the combinators are a regression for possibly-empty half-open loops unless the caller guards `n == 0`. Add a safe half-open helper, e.g. `count_each(f: Int -> Unit !{e}, n: Int)` / `count_fold` (iterate `[0, n)`, no-op when `n <= 0`), or an exclusive-range constructor `upto(n)`. Then update the README Iteration Combinators note (currently documents the guard workaround) to recommend the helper. Low-risk stdlib addition; effect-poly like the existing grid.

- [x] `P2` Mutable containers module + `MutMatrix`. **LANDED 2026-07-09.** Extracted `MutVec` (+ `mutvec_new/get/set`) out of the implicit prelude into a new `stdlib.mutable` module (mutation is now an explicit, opt-in import) and added a row-major `MutMatrix a` there (bounds-checked `mutmatrix_get`/`set`, `mutmatrix_new`/`rows`/`cols`, backed by one `MutVec`). Every bare `MutVec` consumer now imports `stdlib.mutable` (5 stdlib tests, the value-restriction fixture, astar + neural_network_train_xor). `examples/digit_recognizer/recognizer.sprout` rewritten to hold weights in a `Net` record of two `MutMatrix` + two `MutVec`, deleting the flat-vector index math (`iw1`/`ib1`/`iw2`/`ib2`, `off_*`, `n_weights`); training output byte-identical. No compiler code used `MutVec` (comments only), so the seed is IR-unchanged.

- [x] `P2` Growable `MutVec` — **LANDED 2026-08-15**, design in [docs/growable-mutvec-v0.md](docs/growable-mutvec-v0.md). One new builtin (`vector_push`, in-place realloc of the `VectorVal` backing array, doubling from 8) plus `mutvec_push` / `mutvec_empty` in `stdlib.mutable`. `mutvec_empty` needed no builtin — `vector_empty` was already a prelude extern backing `Vec`. Growth is in place, so every copy of the handle sees it; `len` stays live-length and bounds stay checked against it. Third write-barrier site — see the amendment to the generational-GC entry above.
- [ ] `P3` **Growable `MutVec` — the deferred operations.** Scoped out of the 2026-08-15 landing on the "add functions when a caller needs one" rule, listed so the omissions do not read as oversights. (a) `mutvec_with_capacity(n)` / `mutvec_reserve(v, n)` — skip the regrowth when the size is known; the consumers are per-frame stores where the doubling reallocations *are* the cost. Both need one new builtin (`vector_reserve`). (b) `mutvec_pop`, `mutvec_truncate(v, n)`, `mutvec_clear(v)` — `truncate 0` is hand-written in the downstream `world_reset`, and individual element removal is blocked on it in that consumer's ECS design. `truncate`/`clear` need `vector_truncate` (a `->len` store, trivial); `pop` composes from `at` + `truncate`. (c) The open question the landing does not answer: an ECS whose `world_new(cap)` fixes every component column's capacity at once only benefits if `world_spawn` can grow columns it does not own — engine-side, and it decides whether growth fixes one store or all of them.

### 6) Modules and Packaging

- [x] `P0` Implement real module namespaces (remove flattened global import model).
- [x] `P1` Move global string helpers into namespaced stdlib module(s).
- [ ] `P1` Define package/dependency conventions for third-party modules. **Design draft: [docs/packaging-v0.md](docs/packaging-v0.md)** (multi-repo packaging + cross-package coherence). Direction recorded (non-normative, per-phase approval pending): strict compile-time coherence (orphan rule extended across packages), single-version selection with a real/loud resolver, incompatible majors as distinct *explicit* package identity (Go `/v2`-style), git sourcing + reuse of the `.iface`/`.bc` artifact cache, graph-wide compiler-version unification. Package-qualified identity is the spine — it generalizes `module-qualified-type-identity` and dissolves the current "dotted non-`stdlib.` import resolves to `Nothing`" gap (subsumes the cross-`examples.*`-module-resolution note below) as its degenerate single-package case. Phased plan in §10 of the doc, semantics-before-mechanics.
- [ ] `P2` Dedup `extern fn` declarations in the bundler (root-cause fix for PR 14's lowering-side workaround): the typed AST that reaches `ast_to_ir.translate_program` currently contains the same `TExternFnDecl` once per importing module — e.g. `bytes_empty` appears in every importer of `stdlib.bytes`. The IR codegen path (M3 PR 14, perf/ir-extern-fn-declares) deduplicates via a `seen: Set String` in `lower_extern_decls_loop`, but direct codegen has its own `build_extern_sigs_from_decls` that also has to defend against duplicates (`dict_set` semantics make this implicit but identical-signature). The right fix is in `bundler.sprout` (or wherever the bundled `ast.Program` is assembled): collapse same-name `ExternFnDecl` nodes into one canonical decl, with a check that signatures match across importers. Saves work in both codegen paths and is the kind of representation invariant that pays back as more passes get added.
- [ ] `P2` stage-1 module loader does not resolve `examples.*` imports (only `stdlib.*` is handled), so a multi-file example program that imports a sibling example module fails to link — e.g. `examples/sentry_issue_browser.sprout` importing its `sentry_issue_browser_tui` helper; library-style example modules with no `fn main` also fail at the entry point. Both are tracked in `XFAIL_EXAMPLES` in the `compile-examples-stage1` justfile task; the fix needs a design decision on cross-example module resolution.

### 6.5) OS and Process Primitives

- [x] `P2` Add `stdlib.process` with `ProcResult` ADT and `proc_run` / `proc_run_stdin` (2026-05-22): `stdlib/process.sprout` owns `ProcResult Int String String` (exit_code, stdout, stderr). Public API takes `List String` (matches list literals) and delegates to `proc_run_vec`/`proc_run_stdin_vec` C functions (via `vec_from_list`). C implementation in `sprout_runtime.c` uses fork+execvp+poll for deadlock-safe separate stdout/stderr capture. Sprout-native tests in `tests/stdlib/test_process.spr`; example in `examples/process_demo.sprout`. Associated fixes: `find_ctor_tag_by_name` gains a leaf-name suffix fallback for non-prelude ADT constructors registered only by qualified name; stage-1 `codegen.sprout` updated to also register the leaf name alongside the qualified name (takes effect after bootstrap).

- [x] `P1` Fix design gap: adding a new stdlib `extern fn` was a ~5-file change (2026-05-22). Root cause: `ExternFnDecl` was missing from the Python stage-0 compiler's symbol collection (`module_loader.py`) and typechecker env-building (`typechecker.py`); `codegen_llvm.py` required a hand-written `EXTERN_SIGS` entry plus correct LLVM name mapping. Fixed: `module_loader.py` now registers and renames `ExternFnDecl` like `FnDecl`; `typechecker.py` pre-scans `ExternFnDecl` to build env entries from the AST; `codegen_llvm.py` auto-derives `FnSig` entries from `ExternFnDecl` nodes at compile time, mapping qualified Sprout names (e.g. `stdlib.process.proc_run_vec`) to unqualified C symbols (`proc_run_vec`). Adding a new stdlib `extern fn` now requires only `stdlib/X.sprout` (declaration) + `runtime/sprout_runtime.c` (implementation).

- [ ] `P2` Organize OS-level primitives into a coherent `stdlib.os` (or split `stdlib.io` / `stdlib.env`) package: `read_file`, `write_file`, `env_get`, `argv_get` are currently in `prelude.sprout` (globally available) but logically belong in a namespaced OS/IO module. Migration requires updating all stdlib and example callers, rebuilding stage-0/stage-1, and deciding whether to keep prelude re-exports for backwards compat. Also a home for future additions: `list_dir`, `path_join`, `path_exists`, `make_temp_file`, `delete_file`. Prerequisite: `module prelude` header landed (to avoid symbol collision fallout).
- [ ] `P2` Add `proc_shell(cmd: String) -> ProcResult !{IO}` to `stdlib.process` when a concrete use case requires shell features (pipes, redirects, glob). Deferred — `proc_run` covers all current needs. Implementation: `argv = ["/bin/sh", "-c", cmd]` forwarded to `sprout_proc_run_impl`.
- [x] `P2` Add `stdlib.args` — a small pure `--key=value` argv parser (2026-08-03). Motivation: named CLI arguments in place of load-bearing positional argv slots (order-free, collision-proof, self-documenting; see the uncharted-suns boot-config that read ~18 slots by index). `stdlib/args.sprout` owns `Args (Dict String) (List String)`; the pure `parse(List String) -> Args` (all test coverage in `tests/stdlib/test_args.spr`) plus a one-line IO wrapper `parse_argv() = parse(argv_all())`, and total accessors `arg_get`/`arg_str`/`arg_int`/`arg_flag`/`positionals` each defaulted so absence is total. Grammar: `--key=value` (split on first `=`), bare `--key` flag, else positional; duplicate keys last-win. Deliberate non-goals: space-separated `--key value`, short `-k` (→ positional), `--` end-of-options terminator (lone `--`/empty key ignored). Example: `examples/named_args_cli.sprout`.
  - [ ] `P3` Follow-up (fuller argparse flavor): an optional declared `Spec` layer over `stdlib.args` — declare expected args (name, kind, default, required, help) to get `Result`-typed validation of unknown/missing/bad-int args and an auto-generated `--help` string. Deferred; the bag-of-args core above covers the boot-config need without it.

### 7) Tooling and Developer UX

- [ ] `P2` **`just gate-audit` derives "CI runs task X" by grepping the workflow's COMMENTS as well as its `run:` lines (2026-08-15).** `justfile`'s `ci_tasks=$(grep -oE 'just +[a-z][a-z0-9-]*' "$CI_WORKFLOW" …)` matches anywhere in `.github/workflows/ci.yml`, so workflow prose invents requirements. Two failure modes, both hit for real while adding the `windows` job: a task named only in a comment counts as CI-run (`just windows-probe`, a purely local diagnostic), and the ENGLISH WORD "just" manufactures a task name — "rather than just a zero exit status" demanded a `gate` entry for a recipe called `a`. Comments were reworded to dodge it; the audit was left alone deliberately. **Fixing it is not a one-liner:** piping through `sed 's/#.*//'` first makes assertion A correct but immediately breaks assertion C, because `just test` appears in ci.yml *only inside a comment* — CI actually invokes the split `test-stdlib-core-stage1` / `test-stdlib-compiler-stage1` plus `ci-fast-gates`. So the audit's current green rests on a comment match, and the real question behind the fix is whether CI should invoke `just test` by name or `test-package-resolution`/`test-stdlib-stage1` belong in `GATE_ONLY_EXCLUDE`. Decide that first, then strip comments.
- [ ] `P2` **`just ir-golden-diff` truncates each file's diff at 40 lines, so DoD #12's "read the
  diff before regenerating" silently shows a prefix.** `scripts/ir_golden_diff.sh:55` is
  `diff --unified=3 "$file_a" "$file_b" | head -40`. Any change touching more than ~15 lines per
  golden overflows it, and the cut is marked only by a bare `---`, which reads as ordinary diff
  punctuation. Found 2026-08-15 during the prelude-extern split: the change removed 19 declares per
  file, the tool displayed through `regex_replace_all_literal` and silently dropped `regex_escape`
  plus all three `crypto_*`. Noticed only by reconciling the reported set against what had been
  deleted and finding four missing. The recipe's own comment says regenerating without reading is
  "how a real regression gets laundered into an 'expected' snapshot" — truncation defeats exactly
  that. **The reliable review is `git diff tests/golden/ir/` AFTER snapshotting**, which is complete
  by construction; consider making that the documented workflow, raising the cap, or at minimum
  printing "(truncated, N more lines)" so the reader knows they are seeing a prefix.

- [ ] `P3` **No cross-module DCE for unused exported module functions.** A program importing a
  module for one function emits every exported function in it. Measured 2026-08-15:
  `examples/tcp_echo_once.sprout` imports `stdlib.terminal` and calls 3 of its wrappers, but its IR
  carries all **17** `define`s plus a `__sprout_init_globals` it did not previously need (from
  `terminal.sprout`'s module-level `let esc`). They are emitted with external linkage, so LLVM's own
  DCE cannot remove them either. Small in absolute terms, but it makes "import a module to use one
  helper" cost more than it should, and it is a standing argument against splitting the stdlib into
  finer modules. Surfaced by the prelude-extern split, which converted bare extern calls into
  wrapper calls.

  **Update 2026-08-15 — this is no longer small, and it now constrains design.** Tier E measured the
  cost when the imported module is not a leaf, and it decided two placements:

  | placement tried | consumer | cost |
  |---|---|---|
  | `env_get` in `stdlib.process` | `examples/io_do_demo` | **+247** lines (12 unrelated defines: `proc_run`, `ProcResult`, the Bytes decode chain) |
  | `env_get` in a leaf `stdlib.env` | same | +23 |
  | `char_to_str` in `stdlib.string` | `examples/tcp_echo_once` | **+2058** lines, ~12% (62 unused `stdlib.string` defines, for one ESC character) |
  | `char_to_str` in the prelude | same | 0 |

  The `stdlib.process` placement also put **11 `@stdlib.process.*` bodies into
  `bootstrap/compile_driver.ll`**, because `infer.sprout` reads one `SPROUT_TRACE_DISPATCH` switch.
  Both were reverted to the cheaper home. The surviving payer is `stdlib/json.sprout`, which had no
  imports and now carries ~2,700 lines of unused `stdlib.string` + `stdlib.bytes` bodies for four
  byte-offset externs at ~85 call sites — accepted as an honest dependency for a text parser, and
  recorded here as the concrete number this entry previously lacked.

  Until this is fixed, the placement rule in [spec-v0.md §3 *Externs are outside the module
  system*](docs/spec-v0.md) has to carry the weight: **an extern may move only to a leaf module, or
  to one its consumers would import anyway.** That is a workaround for missing DCE masquerading as a
  design principle; fixing DCE would let placement be decided on meaning alone.

- [ ] `P3` **`import stdlib.prelude` silently doubles the prelude in the bundle.** The prelude has
  no `module` header, so `any_has_module_name` (`bundler.sprout:540`) is false for a file whose only
  import is `stdlib.prelude`, and the bundler does not auto-prepend — the explicit import supplies
  the single copy. Add *any* import of a module-bearing file and the auto-prepend switches on, the
  program gets two copies of every prelude instance, and it fails with `Overlapping instances for
  Semigroup`. Hit 2026-08-15 in `test_dbe_synthetic.spr` and `test_lexer_slice.spr`; both had a
  vestigial `import stdlib.prelude as prelude` whose alias was never used, and both were fixed by
  deleting it. No such import remains in the repo, so this is currently latent — worth a lint that
  rejects `import stdlib.prelude` outright, since there is no case where it is correct.

### Prelude extern relocation — status and open questions

**Done 2026-08-15.** `stdlib/prelude.sprout` declared **85 `extern fn`s**, every one of them
globally reachable and emitted as a `declare` into every program with a module header. **42 have
moved out; 43 remain** (`grep -c '^extern fn' stdlib/prelude.sprout`). The original plan projected
44; the difference is `double_to_string`, which turned out to be called by the prelude's own
`instance ToString Double`, and the `char_to_str`/`char_from_codepoint` pair, which was moved and
then measured back. The rule that governs what stays is normative in
[spec-v0.md §3 *Externs are outside the module system*](docs/spec-v0.md).

| tier | what moved | destination |
|---|---|---|
| — | `print_int`, `read_lines`, `read_int_lines`, `string_join_newlines` | **deleted** — zero callers; `read_lines` had no C implementation at all, so it could never have linked |
| A | `regex_*` (5), `crypto_*` (3) | already declared in `stdlib.regex` / `stdlib.crypto`; the prelude copies were redundant |
| B | `bytes_*` (7) | `stdlib.bytes` |
| C | `vec_make_filled`, `vector_mutset`, `vector_get_direct` | `stdlib.mutable` |
| D | `term_*` (8) | `stdlib.terminal` |
| E | `read_file`, `write_file` / `env_get` / `time_now_micros`, `wall_time_micros` | new leaf modules `stdlib.fs` / `stdlib.env` / `stdlib.time` |
| E | `str_byte_len`, `str_slice_bytes`, `str_starts_with_at_byte`, `str_split_lines`, `split_words` | `stdlib.string` |
| E | `double_to_bits`, `double_from_bits` | `stdlib.math` |

One latent defect was fixed on the way: the prelude declared
`regex_replace_all_literal(s, pattern, replacement)` while the C takes
`(pattern, replacement, text)`. All three parameters are `String`, so both declarations typechecked
and the **wrong one was the globally reachable one** — a caller who never imported `stdlib.regex`
could follow the prelude's parameter names and get silently wrong output with no diagnostic.
`just check-extern-signatures` (`scripts/check_extern_signatures.sh`) now forbids the shape: one C
symbol, one `extern fn` declaration, repo-wide.

Coverage closed while relocating, all of it previously absent: `stdlib.crypto` and
`stdlib.terminal` had **no test anywhere**; `read_file`, `write_file`, `env_get`,
`wall_time_micros` and `split_words` had no test that treated them as the subject rather than as
setup for something else. New: `test_crypto`, `test_terminal`, `test_fs`, `test_env`,
`test_string_words`, plus the wall-clock half of `test_time`.

**Open question — relocate `Vec` / `Dict` / `Set`?** The 19 `vector_*` / `map_*` / `native_set_*`
externs that remain are pinned by mechanics, not by policy: the prelude's own combinators call
them, and the prelude cannot import (its `ParsedModule` is built with `Nil` imports, hard-coded at
`bundler.sprout:536`), so an extern cannot leave while its wrapper stays. Moving them means moving
the **types and combinators** — which, unlike an extern, forces every caller to import, touching
essentially every file that writes a `Vec` literal or calls `dict_get`. Explicitly **undecided**:
recorded as a question, not a committed task.

**Open question — the remaining `str_*` and `to_double`.** `str_len`, `str_char_at`, `str_find`,
`str_starts_with` and `to_double` satisfy none of the three "stays" criteria (no prelude-body
caller, not intrinsics, not obviously core) yet were deliberately left in place: `str_len` alone
has 44 consumer files, and they are the operations a beginner reaches for. The honest statement is
that the rule has a fourth, ergonomic criterion — *char-indexed string operations and numeric
conversion are language core even when the prelude does not call them* — which is written into the
spec but is a judgement call, not a mechanical test.

**Orphan.** `string_join_newlines` was added 2026-05-11 for `codegen.sprout` (see the entry at
line ~1757); that file no longer exists and the builtin had no other caller, so the declaration was
deleted. The **C implementation is still in `runtime/sprout_runtime.c`** and is now unreachable
from Sprout — a candidate for removal, deliberately not bundled into this change.

- [ ] `P3` **`just test-file` reports a false green for tests that need `SPROUT_STDLIB_ROOT`**
  (2026-08-14). REPL/analysis-service tests guard on `env_get("SPROUT_STDLIB_ROOT")` and no-op
  without it — `test_repl_constraint_check.spr` and `test_repl_type_vocabulary.spr` both print
  `0 passed, 0 failed` followed by `SUITE PASSED`, i.e. an apparent pass that executed no
  assertions. Only the full `just test` runner sets the variable (`justfile:416`), so the trap is
  specific to fast iteration, which is exactly when a green is trusted. Hit for real while
  RED-verifying the type-vocabulary fix: the "failing" test reported SUITE PASSED before any
  implementation existed. Fix: set `SPROUT_STDLIB_ROOT="{{stdlib_root}}"` in `_test-file` (matching
  the full runner), or have the guard fail loudly rather than skip. A skip that reports success is
  the worst of the three options.
- [x] `P1` `main() -> Int` never propagates its return value to the process exit code. **STALE — verified FIXED 2026-08-07, no longer reproducible.** Re-tested both repros from this entry against a stage-1 built from the committed seed. The effectful one works: `fn main() -> Int !{IO} = do { print("hi"); 7 }` compiled and linked exits **7**, not 0. The pure one, `fn main() -> Int = 3`, is now *correctly rejected* at check time (`ERROR: check: Executable entrypoint \`main\` must declare the {IO} effect`) rather than silently exiting 0 — so its premise no longer exists either. Consequently the "practical impact" paragraph below is also void: `just lint` and `just fmt-check` CAN fail via CI, and indeed `fmt-check` runs in `ci-fast-gates` and gates real violations (it caught a deliberately malformed test fixture during the 2026-08-07 diagnostic-stream work, which is direct evidence the exit-code path is live). Left as a closed entry rather than deleted because the entry's own reasoning — that `fmt_driver.sprout` was the only consumer of `main() -> Int`, so the path could rot unexercised — remains the right worry, and is now covered by `fmt-check` being a CI gate. Original report follows. Discovered 2026-07-20 while wiring the `staircase-of-doom` lint rule into `fmt_bin lint`. Minimal repro: `fn main() -> Int = 3` and the effectful `fn main() -> Int !{IO} = do { print("x"); 7 }` both exit 0, not 3/7 — reproduced at both `-O0` and `-O2`, so not a clang-optimization miscompile. This is base-level codegen/runtime, not specific to `fmt_driver.sprout` or do-blocks. **Practical impact: `just lint` and `just fmt-check` cannot currently fail via CI even when real violations are printed** — both recipes are `rg --files | xargs fmt_bin <subcommand>`, relying entirely on `fmt_bin`'s process exit code (unlike `just test`'s harness, which double-checks via a `SUITE FAILED` text grep rather than trusting exit code alone). `fmt_driver.sprout` appears to be the only consumer in the codebase of the "`main() -> Int` exit-code support" infrastructure noted as added 2026-05-20 (Phase 12 formatter/linter landing) — everywhere else uses `main() -> Unit !{IO}` plus text markers, so this path has likely been silently broken since inception without being exercised. Needs a root-cause investigation in the codegen/runtime `main`-wrapping path before `just lint`/`just fmt-check` can be trusted as real gates.
- [ ] `P1` Stack-overflow diagnostic v2 — map native backtrace frames to **Sprout source locations** (`file:line`). v1 shipped 2026-06-27 (branch `feat/stack-overflow-panic`): the runtime now catches native stack overflow on an alternate signal stack (`sigaltstack` + `SA_ONSTACK`) and panics with `[sprout] fatal: stack overflow` plus a native backtrace that names the recursing function, instead of a silent exit-139 SIGSEGV. v1 already paid for itself — relinking the typed self-compiled compiler against the new runtime instantly named flip-blocker #2's culprit (`stdlib.compiler.lexer.tokenize_from` recursing without TCO), which `lldb` could not unwind. v2 turns the symbol+offset frames into source positions so the panic points at the exact `.spr` line. Options: (a) a per-thread shadow call stack of `(fn, line)` pushed/popped in codegen-emitted prologues/epilogues (precise, but adds per-call cost — measure first); (b) emit DWARF line tables from codegen and symbolize offsets post-hoc (no runtime cost, larger codegen change); (c) a lighter `__sprout_set_current_loc` breadcrumb updated at statement granularity. Pick after a cost measurement; (b) is the principled end state. Keep v1's named-frame backtrace as the fallback when source info is absent. Regression: extend `just stack-overflow-smoke` to assert a `file:line` appears once v2 lands.
- [ ] `P2` REPL SIGSEGV on a tuple that nests let-bound tuple variables. Reported 2026-06-26. Repro in the REPL: `let t1 = (1,3,"foo",true)` (ok), `let t2 = (t1, t1)` (ok), then evaluating `t2` → `eval_expr_in_source: runtime error (exit 1): [sprout] SIGSEGV (no current function set)`. Flat tuples are fine — `(1,3,"foo")` and `(1,3,"foo",true)` display correctly, and even `t1` alone displays. The crash needs the nested case (a tuple whose elements are previously let-bound tuple variables). **NOT a codegen bug:** the equivalent compiled program `module main / fn main() -> Unit !{IO} = do { let t1 = (1,3,"foo",true); let t2 = (t1,t1); print(t2) }` renders `((1, 3, foo, true), (1, 3, foo, true))` correctly and exits 0 under BOTH `--use-ir-codegen` and `--emit-ir`. So the fault is in the REPL's per-expression eval path (`analysis_service_driver.sprout` `op_eval_expr_in_source` / `eval_expr_in_source`, ~line 360/562), most likely in how it synthesizes the throwaway program that references prior session let-bindings (`t1`, `t2`) for a nested-tuple value — the `SIGSEGV (no current function set)` runtime string points at a missing/!corrupt function context in the generated harness rather than the value codegen. Independent of the #93 print(tuple)→to_string change (which only affects compiled `print`). Next step: dump the source the REPL synthesizes for `t2` (session imports + decls + sentinel binding) and compile/run it standalone to localize.
- [x] `P2` Extract the C runtime out of the `runtime_c = """..."""` string in `sprout/cli.py` into a standalone `runtime/sprout_runtime.c` source file (2026-05-17). All `build-stage*`, `test-stdlib-stage*`, `compile-examples-stage*`, and `compile-native` justfile recipes now link `runtime/sprout_runtime.c` directly — no Python invocation needed for the C runtime. `just update-runtime` regenerates the file when the embedded template in `cli.py` changes. `scripts/gc_safety_check.py` updated to read the file directly (now reports correct line numbers). Remaining dynamic piece (analysis bridge with embedded Python executable path) is still rendered by `cli.py` for native REPL builds only.

- [x] `P2` Replace `just run` and `just run-stdlib` with a Python-free compile-and-run pipeline (2026-05-17): both recipes now invoke `./compile_driver_bin_stage1 --emit-ir` → `clang runtime/sprout_runtime.c` → execute the resulting binary, using a `trap`-deleted temp file under `/tmp`. `run-stdlib` is now an alias for `run` (stage-1 always includes stdlib). Guard added: if `compile_driver_bin_stage1` is absent, prints a helpful error and exits 1. Verified: `just run examples/hello.sprout` prints `hello from sprout` in 1.6 s wall time.

- [x] `P1` Add REPL (parse/typecheck/eval loop).
- [x] `P1` Add better diagnostics for module/import/export errors with source context.
- [x] `P1` Parse-error diagnostics: correct line numbers + reserved-keyword hint (2026-07-16). Two defects surfaced while building the channels fix: (1) a parse error reported a line number shifted UP by the number of leading `module`/`import`/comment/blank lines, because header stripping DELETED those lines before tokenizing (`--emit-ir` of a file with a leading `module` line + a keyword-name error reported line N−headers, actively pointing at an unrelated earlier declaration); (2) `fn wrap` (or any reserved keyword in name position) produced a bare `Expected function name` that read like malformed syntax instead of naming the reserved word (`wrap` is the newtype keyword). **Root cause of (1): the strip logic was DUPLICATED in 8 places** (`compiler`, `bundler`, `analysis_service_driver`, `compile_driver`, `driver`, `lower_driver`, `type_driver`, `module_loader`), each deleting leading header lines and each with the same line-shift bug (plus divergences — only the bundler copy stripped `#` comments; the analysis service had compensated with a `count_header_lines` offset added back to symbol locations). **Fix: consolidated all copies into a single `source.strip_headers` that REPLACES each stripped header line with a blank line, preserving original line numbers.** The analysis-service offset workaround was removed (positions are now already correct; keeping it would double-count). (2): `expected_ident` now names a reserved-keyword token in the message. Regression: `tests/stdlib/compiler/test_diagnostic_positions.spr` (line survives header stripping → line 3 not 2; message mentions `keyword`; direct `strip_headers` blank-line-preservation unit assertion). Verified end-to-end through the real `--emit-ir` bundler path: `Expected function name, but 'wrap' is a reserved keyword at 6:4`. Seed fixed-point reached at iteration 2.
- [x] `P2` `fmt_bin fmt` drops the space between any word-like token and a following `[...]` list-pattern/list-literal token — discovered 2026-07-21, fixed 2026-07-21. Root cause: `formatter.sprout`'s `needs_space_word_or_op` forced a space before `{` and `(` but had no case for `[`, so its fallback (`is_word_like(p) && is_word_like(curr)`) always returned `false` for `[` (`TokenSymbolKind`, never word-like) — collapsing the space after ANY identifier, keyword, or literal, not just a wildcard `_` (repro widened past the original single-site report: `x _ [1, 2]` -> `x _[1, 2]`, `foo bar [1, 2]` -> `foo bar[1, 2]`, `x true [1, 2]` -> `x true[1, 2]`, `x 1 [2, 3]` -> `x 1[2, 3]`, all reproduced and fixed). Confirmed Sprout has no `arr[i]` indexing syntax (`[` only ever starts `parse_list_literal`/list-pattern parsing in `parser.sprout`), so there was no legitimate no-space case being protected — fix adds an unconditional `tok_is_punct(curr, "[") -> true` case alongside the existing `{` one. Regression tests in new `tests/stdlib/compiler/test_formatter.spr`. Fixing this also retroactively repaired two corrupted sites already on master from the `list-shape-pattern` corpus-cleanup PR (#224): `stdlib/compiler/infer.sprout`'s `ast.AliasDecl name[param]` (missing space, silently shipped) and an unrelated pre-existing conformance fixture `tests/conformance/type_error/missing_nested_instance_eq.spr`'s `if[()]`. The one site that had been worked around (`stdlib/compiler/lint_rules.sprout` `is_two_branch_match`) is converted back to `[_, _]` sugar; `just fmt` run twice confirms stability (no further reformatting).
- [ ] `P3` **`fmt_bin fmt` drops the space between two adjacent parenthesized type atoms** — discovered 2026-07-31 while adding `examples/existential_widget.sprout`. A constructor with two consecutive function-typed fields, `Widget s (s -> s) (s -> String)`, is reformatted to `Widget s (s -> s)(s -> String)`, which reads like function application rather than two separate fields. Same root-cause family as the `[...]` fix above (BACKLOG line for the `[` case): `formatter.sprout`'s `needs_space_word_or_op` has no case for a `)` immediately followed by `(` — `)` is `TokenSymbolKind` (not word-like), so the `is_word_like(p) && is_word_like(curr)` fallback returns `false` and the space is collapsed. Fix: add a case forcing a space when the previous token is `)` and the current is `(` (adjacent grouped atoms). Confirm no legitimate no-space case is protected (Sprout has no call syntax where `)(` is meaningful in a *type* position). Add a formatter regression test in `tests/stdlib/compiler/test_formatter.spr`.
- [x] `P2` Add formatter/linter baseline.
- [~] `P2` Improve formatter/linter beyond the baseline (structural formatting and broader lint rules). `stdlib/compiler/lint_rules.sprout` adds a "Lint" category alongside `formatter.sprout`'s text-based "Style" checks — built on the real parser AST (`parser.parse_program`), no type inference needed. Wired into `fmt_bin lint` (`stdlib/compiler/fmt_driver.sprout`), additive to the existing 4 checks.
  - Rule 1, `staircase-of-doom` (landed 2026-07-20, polished 2026-07-21): detects a chain of nested 2-branch matches, each peeling one constructor off a `Maybe`/`Result`-like value, that `docs/idiomatic-sprout.md` calls out to flatten with `let..else`; scoped to binding-constructor patterns and excludes `Cons`/`Nil` list-walk recursion (own rule now, see rule 3 below) and already-`let..else` chains (desugars to the identical `MatchExpr` shape as a hand-written match — no distinguishing AST node — told apart via a source-text sniff at the match's own position, `is_written_as_match`). Minimum chain depth parameterized (`min_staircase_depth = 3`). Chains that have the staircase shape but where a terminal branch depends on its own failing pattern's binding are still reported; since Tier 1b (2026-07-27) these are pointed at the binding-else form (`else <pat> -> <handler>`, which brings the payload into scope) rather than the old soft "restructure" nudge.
  - Rule 2, `redundant-vec-from-list` (landed 2026-07-21): flags a hand-written `vec_from_list(<bare list literal>)` sitting in a position `stdlib/compiler/desugar_ctx.sprout`'s Vec-context desugar pass already recognizes — the checker would wrap a bare literal there on its own, so an explicit wrap is a no-op. Detection reuses the checker's exact Vec-context computation (same `fn_idx`, same if/match threading), extracted out of `checker.sprout` into `desugar_ctx.sprout` (a leaf module depending only on `ast`/`source`/`string`, no typechecker) — `checker.sprout` now delegates to it unchanged. Corpus-clean at landing (the sites from commit `e845b02` were already manually converted); ground-truth-verified against a real compiling site (`tests/stdlib/test_vec_literal_coercion.spr`'s `explicit_wrap`).
  - Rule 3, `list-shape-pattern` (landed 2026-07-21): flags a hand-written `Cons`/`Nil` chain that matches an EXACT fixed length, e.g. `Cons a (Cons b Nil)` (reads as `[a, b]`) — scoped to fixed-length chains only. A corpus scan of the first, unscoped design (flag every `Cons`/`Nil` node) found 1484 findings across `stdlib`/`examples`, including the rule's own source file; reclassifying by what the chain's tail resolves to showed ordinary head/tail recursion (`Cons h t`, var tail) and discard-the-rest (`Cons h _`, wildcard tail) together are ~99% of all hand-written `Cons`/`Nil` patterns in this codebase — `[h | t]`/`[h | _]` convey nothing those don't already say, so flagging them would make the rule unshippable (fires on the single most idiomatic way to write list recursion, including on its own implementation). Rescoped to fire only when the chain's tail resolves to a literal `Nil` (never a bound variable or wildcard); the `Cons h _` ("match the producing call directly"-adjacent, `[a, b | _]`-sugar-able) case was deliberately deferred, not folded in — see below. Corpus count under the rescoped rule: 40 (all spot-checked genuine, e.g. `iface_codec.sprout:54`'s `Cons c Nil -> …` paired with an unflagged `Cons c rest -> …` sibling); zero false positives on real `[…]` sugar sites. Same `SourcePos`-granularity discriminator as rule 1 (`[…]` sugar shares one position, the `[` token's; a hand-written node carries its own token's position) told apart via `is_written_as_ctor`. **Corpus cleanup landed 2026-07-21** (separate commit, stacked PR): 39/40 sites converted to `[...]` sugar, sibling var-tail arms in the same match converted alongside for consistency (e.g. `Cons c rest` → `[c | rest]`) even though the rule itself doesn't flag them — standalone head/tail recursion elsewhere (untouched by the rule) was deliberately left as `Cons x rest`, not swept along. 1 site (`lint_rules.sprout` `is_two_branch_match`) was initially left as hand-written `Cons _ (Cons _ Nil)` to dodge a `just fmt` space-stripping bug (see the "Tooling and Developer UX" item above) — fixed 2026-07-21 alongside that bug and converted to `[_, _]` sugar, so all 40 corpus sites are now clean. Verified via `just refresh-seed-clean` reaching a fixed point at iteration 2 (compile_driver recompiling itself through every touched file) plus the full test suite.
  - Rule 4, `list-prefix-pattern` (landed 2026-07-21): the `Cons h _` → `[h | _]` case deferred from rule 3 — flags a hand-written `Cons`/`Nil` chain whose tail resolves to a `WildcardPattern` (discard-the-rest), mutually exclusive from rule 3 by construction (a chain's tail can resolve to literal `Nil`, a wildcard, or a bound variable, never two of those at once). Shares rule 3's `is_written_as_ctor`/`drop_desugared_list_patterns` discriminator and message-shape. Full-corpus count (including `tests/`, not just `stdlib/`): 65. **Corpus cleanup landed 2026-07-21** (separate commit, stacked PR): 57/65 converted to `[...]` sugar (40 in `stdlib/`, 17 individually-vetted `tests/` sites whose hand-written form was incidental helper code, not the thing under test — sibling var-tail arms in the same match/`let..else` chain converted alongside for consistency, matching rule 3's cleanup precedent). 8 sites deliberately left hand-written because the hand-written form IS the test subject: `tests/stdlib/test_list_pattern_runtime.spr:50` (sugar/legacy parity fixture), `tests/stdlib/compiler/test_parser.spr:23,70,86,102` (raw-constructor-pattern parsing coverage), `tests/stdlib/compiler/test_lint_rules.spr:20,25,34` (described at the time as this rule's own hand-written-vs-sugar fixtures — **that description is wrong**, corrected 2026-08-12: those three sites are ordinary helpers `first_line`/`first_rule`/`first_message`, and the file's actual rule fixtures are string literals fed to `lint_ast`, immune to file-level linting; see the lint-suppression-pragma entry below). Verified via `just refresh-seed-clean` reaching a fixed point at iteration 2, `just lint` showing exactly those 8 sites remaining, and the full test suite.
  - Both traversals (rule 3 and rule 4) retrofitted 2026-07-21 to use a new general `concat_map` prelude combinator (`fn concat_map(f: a -> t, xs: c a) -> t where Functor c, Foldable c, Monoid t = mconcat(map(f, xs))`, next to `mconcat` in `stdlib/prelude.sprout`) instead of hand-rolled `Nil -> Nil | Cons x rest -> list_append(f(x), walk(rest))` recursion, per the project's own "use idioms we're linting for" standard — the AST-walk fan-out (`_decls`/`_exprs`/`_branches`/etc.) is exactly the "map each element to a list of findings, then flatten" shape `concat_map` names. Same `mconcat`-style O(n²) tradeoff comment applies (fold-over-`++` copies the growing accumulator; fine at AST-walk sizes).
  - Found via `concat_map`'s own test-authoring, then fixed (2026-07-21): an unsatisfied `Monoid Vec` constraint (Vec has `Semigroup` only, no `empty()`) used to compile cleanly and crash at RUNTIME as a "non-exhaustive match" instead of being caught at typecheck. Root cause: `stdlib/compiler/infer.sprout`'s `build_constrained_fn_dicts_via_field` — the return-position dict-resolution post-pass (item 4's §13.3(B) machinery, `[[project_canonical_identity_campaign]]`-adjacent) — silently DROPPED a headed constraint's dict when `find_inst_in_return_type` found no instance, instead of erroring like its sibling direct-call path (`check_instance_for_marker`'s `InferErr("No instance of ...")`). The dropped dict then null-filled downstream (`ast_to_ir.sprout`'s `__unresolved_` sentinel, designed for PROVABLY-unreachable dict slots) and only surfaced as a match failure wherever the missing dict's method got called for real. Fixed at the existing dispatch-verify safety net instead of the resolver itself (lower blast radius, no IR/seed perturbation): `stdlib/compiler/verify_dispatch.sprout`'s `check_constraints` now pairs constraints to injected dicts BY CLASS (`take_by_class`, mirroring `infer.sprout`'s own `take_dict_for_class`) instead of positionally, so a dropped dict no longer silently misaligns everything after it; a class with no matching dict AND a `theta`-concrete constraint var (`check_missing`) is now `VerifyMismatch("No instance of {class} for {head}")`, reusing dispatch-verify's existing fatal-by-default wiring in `compile_phase_check` (already the ONLY place `verify_dispatch.verify_program` gates `--phase check`). Regression: `tests/conformance/type_error/unsatisfiable_return_position_constraint.{spr,err}`. Verified via full `just test` (no new failures, no fixture flipped from OK to xfail) — the risk this fix runs is rejecting a previously-accepted, actually-legitimate program; the full suite is the check for that, not just the new fixture going green. Seed refreshed to a fixed point at iteration 2.
  - Roadmap for further `docs/idiomatic-sprout.md` coverage (not yet started): "Match the producing call directly" (a `let`/do-bind immediately followed by a match on that single, otherwise-unused variable) and "Collapse a trivial `do` block" (a block whose only step is a bind immediately returned unchanged). The remaining sections of that doc (illegal-states-unrepresentable, `wrap`, keep-effects-at-edges, `|>` chaining, combinators-over-index-loops) are design-level or too fuzzy for a reliable syntactic check.
  - Also open: a config file for per-rule enable/disable (not yet justified at 3-4 rules), and autocorrect (needs an AST-aware rewriter; today's formatter is line-based text transform only).
- [ ] `P3` **Rust-style lint suppression pragma, then wire `lint` into CI.** `just lint` is permanently red — 10 findings across 4 files — and `lint` is **not** in `ci-fast-gates` (that gate list runs `fmt-check`, not `lint`), so violations cannot fail CI and the red set drifts unnoticed. Ground truth re-derived 2026-08-12; it had already diverged from the rule-4 cleanup note above. Two of the four files violate **deliberately**, because the raw form *is* the test subject, and cannot be rewritten without deleting the test:
  - `tests/stdlib/test_list_pattern_runtime.spr` (5 findings) — `head_legacy`/`pair_legacy`/`head_tail_legacy`/`wrap_arg_name_legacy` exist to prove raw `Cons x Nil` and `[x]` sugar agree at runtime.
  - `tests/stdlib/test_vec_literal_coercion.spr:29` (1 finding) — its own comment states it is "the shape the redundant-vec_from_list lint rule targets".

  The other two are **incidental** and need only a `[h | _]` rewrite, no policy: `tests/stdlib/compiler/test_lint_rules.spr:20,25,34` (helpers, not fixtures — see the correction in rule 4 above) and `tests/stdlib/test_existential_prefix_compound.spr:43` ("prefix" there means explicit-prefix *existentials*, a type-system term, not list prefix patterns). Evidence that an unwired gate rots: `test_parser.spr`'s 4 sites are clean today, while the existential site landed 2026-07-31 — ten days *after* the rule-4 cleanup — so the red set drifted in both directions with nothing watching.

  **Direction chosen 2026-08-12 (Rust-inspired):** a same-line trailing comment naming the rule, e.g. `| Cons a (Cons b Nil) -> …  # lint: allow(list-shape-pattern)`. Rule name **mandatory**: Rust's `#[allow(name)]` requires it while ESLint/clang-tidy/golangci-lint permit a blanket form, and a blanket "allow everything here" would have silently swallowed the two incidental findings above. Same-line rather than next-line because a trailing pragma cannot drift off its target when code moves. Prior art, each verified against its primary source 2026-08-12: Rust `#[allow(unused_variables)]` (item/crate scope, name required); ESLint `// eslint-disable-next-line no-alert` (line/next-line/block, name optional); clang-tidy `// NOLINT(check-name)` plus `NOLINTNEXTLINE`/`NOLINTBEGIN`/`NOLINTEND` (name optional, globbable); golangci-lint `//nolint:linter` (no spaces permitted anywhere in the directive, name optional via `:all`). Consensus is line-scoped, rule-named, comment-based.

  **Implementation sketch:** a post-parse filter inside `lint_rules.lint_ast`, sibling to the existing `drop_desugared_matches` and `drop_desugared_list_patterns` — both already re-read raw source lines via `nth_line` to drop false positives keyed on `(line, rule_id)`, which is this mechanism already, minus a user-facing spelling. No lexer or parser change is needed (comments never reach the AST). The filter must live in `lint_ast`, **not** `fmt_driver`, so that a future `sproutd`/editor lint surface cannot disagree with the gate; `fmt_driver` is the only caller today, which is exactly why the choice is free now and expensive later. Verified 2026-08-12: a trailing `# lint: allow(...)` comment survives `fmt --check` unmoved, and `[h | _]` sugar does silence the rule. Scope limit to state in the docs: `formatter.lint_source` issues carry no rule id (`LintIssue ln col msg`), so the pragma contract covers AST rules only — sufficient here, since all 10 current findings come from `lint_ast`.

  **Ship as one change:** pragma + the two incidental rewrites + `lint` added to `ci-fast-gates`. Green-but-unenforced returns to red, as the drift above demonstrates. Needs a docs home — the lint tool has no documented section anywhere today (rule messages cite `docs/idiomatic-sprout.md` for the *idiom* only); propose a short `## Lint` section in `docs/style-guide-v0.md` covering the rule list and the pragma. Cost is small in code (~40 lines plus tests) but it is a `stdlib/compiler/` edit, so it drags the full reseed / golden-IR / bundle-smoke chain.

  **Deliberately deferred:** Rust's `#[expect(lint)]` — which warns via `unfulfilled_lint_expectations` when the named lint does *not* fire — is a strictly better fit for the two deliberate files, since it turns the suppression into a second assertion that the flagged construct is still present rather than a hole that silently outlives its reason. Held back only to avoid shipping two mechanisms at once; revisit once `allow` has real usage.
- [x] `P3` Refactor trace fork in `stdlib/compiler/compiler.sprout`'s `compile_full_ir_lines`. Resolved via approach (a): new `compile_phase_recheck_timed` is the single-source pipeline body (bundle→prelude→check→lower) writing per-phase timestamps into a `Ref PhaseTimes`; `compile_full_ir_lines_fast` and `compile_full_ir_lines_traced` eliminated; `compile_full_ir_lines` calls `compile_phase_recheck_timed` unconditionally, then reads the Ref and emits the `[phase]` line only when `SPROUT_TIME_PHASES` is set. Phase-isolation GC guarantee preserved. See PR refactor/trace-fork-unify. (Superseded 2026-07-12: `compile_full_ir_lines` and this whole timing path were deleted with the direct backend; re-adding per-phase timing on the typed path is tracked under "Compiler / Stdlib Misc".)
- [ ] `P3` Investigate native formatter inconsistency on nested constructor spacing: `Just(Just(x))` sometimes gets formatted with an inner space (`Just (Just(x))`) and sometimes not, even within the same file. Reproduced 2026-06-10 on `tests/stdlib/test_serialize_primitives.spr` (before that file was deleted as part of the Serialize/Deserialize revert) — running `just fmt-file` deterministically produced a diff that inserted spaces in some occurrences but not others. Suggests an inconsistency in the formatter's nested-call handling, possibly column-budget driven. Add regression coverage with a focused fixture that contains multiple `Just(Just(x))` patterns at different indentation depths.
- [ ] `P1` Fix native formatter newline preservation bug: `fmt_bin fmt` can collapse
  `.sprout`/`.spr` files to a single line. Reproduced on
  `stdlib/compiler/lowering.sprout`, `tests/stdlib/compiler/test_lowering.spr`,
  and `tests/stdlib/test_constrained_container_dispatch.spr`; this also makes the
  pre-commit `fmt-check` hook unusable for affected files. Add regression coverage
  for multiline modules before re-enabling formatter-enforced commits for these paths.
- [ ] `P2` Move `lower_typeclasses` into `run_program` so callers never need to apply it manually: every production call site (cli.py, repl.py, analysis_execution_backend.py) already lowers before calling `run_program`, but tests that call `run_program` directly on an unlowered program silently break whenever new code uses a Foldable/typeclass-constrained function. Verify no caller double-lowers (lowering should be idempotent, but confirm first), then absorb the step inside `run_program` and remove the redundant `lower_typeclasses` calls at each call site.
- [x] `P1` Add `opt --passes=verify` IR validation to `bootstrap-from-seed`, `_test-stdlib`, and `_compile-examples` justfile recipes (2026-05-28). `_build-stage` already had the check. All three emit LLVM IR and then hand off to clang; the verify step runs between IR emission and clang so malformed IR (phi type mismatches, wrong insertvalue types, etc.) is caught with a precise LLVM error rather than a cryptic clang message. `opt` is a hard requirement — the build fails loudly if it is not on PATH. Install via `brew install llvm` (macOS) or `apt-get install llvm-16` (Linux). CI already satisfies this via `llvm-16` in PATH. Pre-commit hook emit-IR smoke tests (section 1) also wired to run `opt --passes=verify` per shape when available. This would have caught the CPR tuple param regression (LLVM phi type mismatch) and the missing-ptrtoint global closure bug.

- [ ] `P3` Wire `opt` optimization passes into the build pipeline: after `opt --passes=verify` is stable in CI, add `opt --passes=mem2reg,instcombine,simplifycfg` before the clang step in `_build-stage` to pre-optimize IR. This decouples Sprout's IR quality from clang's optimizer settings and makes it easier to inspect what survives optimization (e.g. whether CPR unboxed `extractvalue` paths survive, or whether `sprout_field` calls reappear). Also enables `opt --passes=O2 "$TMP_LL" -S | grep -c sprout_field` as a benchmark signal for CPR effectiveness.

- [~] `P1` Execute the staged self-hosting plan below (the standalone `self-hosting-eliminate-python-backlog.md` was retired into this canonical BACKLOG in `beda51e`), with the end goal that compiler/tooling ownership moves from Python into Sprout and the Python path becomes compatibility-only before removal.
  - bootstrap lexer (`stdlib/compiler/source`, `token`, `lexer`) is at Python tokenizer parity
  - bootstrap parser AST types and parser exist in `stdlib/compiler/ast`, `parser`
  - bootstrap HM typechecker stack exists in `stdlib/compiler/types`, `unifier`, `infer`
  - `stdlib/compiler/driver.sprout` emits a flat s-expression AST dump (one decl per line)
  - `tools/dump_ast.py` emits the same format via the Python parser
  - `tests/test_parser_parity.py` runs both on the conformance corpus and diffs output; 11/11 pass (no known divergences — `++` now desugars to `append` in both parsers)
  - **integration seam landed**: `stdlib/compiler/checker.sprout` wraps `infer.typecheck_decls` behind a `CheckResult` ADT; `stdlib/compiler/type_driver.sprout` is an executable that lex→parse→check→dumps typed names; `tools/dump_types.py` does the same via the Python typechecker; `tests/test_checker_parity.py` confirms 6/6 corpus files match (no known divergences — forall generalization fully implemented)
  - **Phase 2 driver landed**: `stdlib/compiler/compiler.sprout` exposes `compile_source`/`compile_file` API; `stdlib/compiler/compile_driver.sprout` is an end-to-end executable; `sprout.cli bootstrap-check` routes at least one real CLI check path through Sprout-owned control flow
  - **FnDecl body inference landed**: `check_fn_body` instantiates the annotation scheme and checks the body against it; unknown-variable/constructor errors silently accepted (builtin leniency), real type mismatches propagate; checker corpus expanded to 6 files
  - **builtin env seeded**: `checker.check_program` starts from a pre-populated env (~25 entries: ADT constructors, string/IO ops, dict/list ops) so body inference resolves calls to common functions without leniency fallback
  - **ClassDecl/InstanceDecl landed**: class methods registered as globally polymorphic schemes; instance method bodies type-checked against method annotations; `type_classes.spr` added to checker parity corpus (now 6/6)
  - **type aliases landed**: `type alias Name = TypeExpr` parsed in Python + Sprout parsers; Python typechecker expands aliases as a pre-desugar pass; bootstrap checker skips `AliasDecl` (transparent to inference); `Set` type added for constraint-satisfaction work pre-work
  - **checker parity corpus expanded 6→8**: `stdlib_fold_filter_map.spr` and `stdlib_mixed_io_result_do.spr` added; `tools/dump_types.py` now seeds prelude ADT constructors + list/dict helpers so corpus files that call them can be type-checked without full module loading
  - **constraint-satisfaction checking landed**: at concrete call sites the bootstrap checker looks up `@class:<method>` markers in env to identify class methods, then verifies a matching `@inst:<class>:<type>` marker exists; missing instances produce a typed error "No instance of X for T in function f"; `tests/conformance/run/instance_check.spr` added to conformance corpus and passes
  - **record field access landed**: `RecordDecl` registers `@rec:<Name>:<field>` markers in env; `RecordExpr` and `GetFieldExpr` inference implemented; `record_types.spr` fixed to use `get p x` syntax, added to conformance corpus and parity corpus (now 9/9)
  - **do-bind monadic unwrapping landed**: `infer.sprout` now unwraps `Maybe a`/`Result e a` at do-bind sites so the bound variable gets the payload type; `append` seeded in checker env and `dump_types.py`; `stdlib_mixed_io_maybe_do.spr` added to parity corpus (now 10/10)
  - **GHC-style forall variable ordering landed**: both Python and bootstrap Sprout checkers now use left-to-right first-appearance ordering for forall vars; bootstrap `scheme_to_string` now renames bound vars to a, b, c… in that order (matching Python); `poly_types.spr` (multi-param ADTs, `Either`, `Pair`) added to checker parity corpus (now 11/11)
  - **module loading wired into type_driver + compile_driver**: `type_driver.sprout` and `compile_driver.sprout` now resolve imports via `module_loader.sprout` before typechecking; `sprout.cli bootstrap-check` passes `stdlib_root` so CLI path fully works; `BootstrapCheckParityTests` (11/11) added to `test_checker_parity.py` to verify `bootstrap-check` CLI output matches Python typechecker
  - **batch mode + import corpus landed**: both drivers now accept `<stdlib_root> <file>...` batch mode with `=== path ===` separators; `test_checker_parity.py` runs each driver once for all 13 files (7.6x speedup: 610s → 80s); `is_lowercase_name` fixed to reject qualified type names (e.g. `json.Json`); `dump_types.py` extended with `load_module_bundle` for import-using files; new `tests/conformance/parity_import/` corpus with 2 import-using files; parity corpus now 13/13 on both checkers
  - **M1 complete (14/14)**: `strip_module_prefix` in `lookup_type_var` fixes qualified annotation mismatch; `alias_env` threading through `typecheck_decls_inner` expands type aliases inline; `VarPattern` fix prevents cross-branch type leakage; all 14 `stdlib/compiler/*.sprout` modules pass `bootstrap-check`; Python recursion limit bumped to 20000
  - **M3 complete**: `stdlib/compiler/bundler.sprout` implements full topological module loading, cycle detection, prelude injection, symbol table building, and name qualification; `bundle_driver.sprout` is a batch-mode executable; `test_bundler_parity.py` confirms 3/3 parity corpus files match Python's bundler output; interpreter extended with Char ordering support; self-hosted parser extended for single-constructor types + class superclasses; `ClassDecl` gains `superclasses` field; module name dotted-ident scanning fixed
  - **M6 manual stage-2 verified**: Sprout-owned codegen now emits enough LLVM IR for `compile_driver.sprout` to build a stage-1 native compiler via `compile_driver_bin --emit-ir` + `clang`; that compiler type-checks the 5-file bootstrap corpus successfully. The stage-2 `Int vs Int` / `Maybe vs Maybe` identity mismatch was fixed by preserving primitive ADT constructor field types in `codegen.sprout` so `TConst String` payload equality lowers to `str_eq` instead of packed pointer identity.
  - **string interpolation mirrored in bootstrap compiler (Phase 3)**: backtick template literals now parsed + desugared in the Sprout self-hosted pipeline: 5 new `TokenKind` variants in `token.sprout`; `scan_template_content`/`scan_interp_body` state machine in `lexer.sprout`; `parse_template`/`collect_template_parts` in `parser.sprout`; `desugar_program` + `desugar_template` pass wired into `checker.sprout`'s `check_program_with_env`/`typecheck_typed`; `StringTemplateExpr` exhaustiveness stubs in `infer.sprout` and `bundler.sprout`; `dump_expr` extended in `driver.sprout`; `string_template_basic.spr` added to parser + checker parity corpus; 26/26 checker parity tests pass. Conformance corpus: `stdlib_string_template_basic.spr` added (prefixed `stdlib_` so bundler injects the prelude's `Cons`/`Nil`/`to_string` needed by `string_concat_many` desugaring). `test_modules.py` exhaustiveness gaps fixed: `render_kind` in 3 test programs got `| _ -> "template"` catchall for the 5 `TokenTemplate*` constructors, and `describe_expr` got `| ast.StringTemplateExpr _ _ -> "template"` for the new constructor.
  - **Phase 9 analysis service binary landed (2026-05-17)**: `stdlib/compiler/analysis_service_driver.sprout` is a JSON-over-stdio analysis daemon; handles `declared_names_in_source`, `exported_names_in_source`, `symbol_inventory_in_source`, `symbol_locations_in_source`, `check_source`, `diagnostics_in_source` (last two require stdlib root as `argv[0]`); stubs `type_of`, `instances`, `eval_expr` to `not_implemented`. Built as `analysis_service_bin` via new `just build-analysis-service` recipe. Crash fixed: `term_read_line`, `term_write`, `json_parse` added to `extern_sigs_list()` in `codegen.sprout` — missing entries silently returned `ret i64 0` causing `sprout_tag(null)` abort. All 5 manual test cases pass. Remaining: parity test suite, unimplemented semantic ops.
  - **Phase 9 `type_of_in_source` implemented (2026-05-17)**: `op_type_of_in_source` in `analysis_service_driver.sprout` appends a sentinel let-binding (`let __repl_source_value = <expr>`) to the source, runs `compiler.compile_source_with_root`, looks up the sentinel in the returned `Dict Scheme`, and renders via `types.scheme_to_string`. Request fields: `op`, `module_source`, `expr` (all required); `stdlib_root` from argv[0] as before. Response: `{"ok":true,"value":"<type string>"}` or `{"ok":false,"error":"..."}`. Manual tests: `42` → `Int`, `true` → `Bool`, `if true then 1 else 2` → `Int`, `Nil` → `forall a. List a`, `add` (user-defined `fn add(x:Int,y:Int)->Int`) → `Int -> Int -> Int`, undefined name → `{"ok":false,"error":"check: Unknown variable: ..."}`, missing `expr` field → `{"ok":false,"error":"missing field: expr"}`. Remaining unimplemented ops: `instances_in_source`, `eval_expr_in_source`, `complete_in_state`.
  - **Phase 9 REPL wiring done (2026-05-17)**: `sprout/repl.py` `cmd_repl(native=True)` now auto-detects `analysis_service_bin` in the project root and sets `SPROUT_ANALYSIS_SERVICE_CMD=<bin> <stdlib_root>` before launching the native REPL binary. `default_analysis_service_bin_cmd()` is the helper. `just run-analysis-service` recipe added for foreground smoke-testing; `just repl` and `just repl-native` recipes added for convenience. `SPROUT_ANALYSIS_SERVICE_CMD` in the environment is respected as an override.
  - **Phase 12 native formatter/linter complete (2026-05-20)**: `stdlib/compiler/formatter.sprout` (pure core) + `stdlib/compiler/fmt_driver.sprout` (CLI, `main() -> Int`) implement `just fmt`/`just lint` natively; 126/126 file parity with Python formatter confirmed; `just build-fmt` / `fmt-native` / `fmt-check-native` / `lint-native` recipes added; `tests/test_fmt_native.py` (11 tests) added. Python `just fmt` and `just lint` remain the canonical recipes until `fmt_bin` is built in CI.
  - **sproutd daemon M0 — lazy runtime.o compile (2026-05-25, revised 2026-05-26)**: `analysis_service_driver.sprout` compiles `sprout_runtime.c → /tmp/sprout_runtime_daemon.o` on first `eval_expr_in_source` call (lazy, not at startup); all subsequent evals use the cached `.o`. `StartupState` carries a `Ref String` for the runtime obj path (`""` = not yet compiled); `ensure_runtime_obj` compiles on demand and caches. Diagnostics/type-of/hover work even when clang is not on PATH. `run_service(stdlib_root)` exported for combined `sproutd` binary. Branch: `sproutd-daemon-foundation`.
  - **sproutd daemon M1 — persistent module cache (2026-05-25)**: `StartupState` extended with a `Ref(Dict...)` module cache field; `build_startup_state` creates the cache and pre-warms it with the prelude so the first request is fast. All semantic ops (`check_source`, `diagnostics_in_source`, `type_of_in_source`, `eval_expr_in_source`) use `compile_source_with_cache` / `compile_full_ir_with_cache` (new exports in `compiler.sprout`) instead of recreating the cache per request. Bundler gains `export type alias` support (`scan_source_info`, `add_decl_to_symbols`, `qualify_decl`); `ModuleCache` exported from `module_loader`. Cache params in `compiler.sprout` are unannotated (inferred) for bootstrap compatibility with old stage1.
  - **sproutd daemon M2 — stateful session protocol (2026-05-25)**: Server-side sessions (`SessionState (Vec String) (Vec String)`) stored in `Ref(Dict SessionState)` inside `StartupState`. Six new ops: `session_create`, `session_update`, `session_eval`, `session_type_of`, `session_diagnostics`, `session_destroy`. Client side: `StatefulSession Int (Vec String) (Vec String)` carries server session ID plus local mirrors of imports/declarations for tab completion; `create_session`, `session_import`, `session_declare`, `eval_in_session`, `type_of_in_session`, `diagnostics_in_session`, `destroy_session` in `stdlib/compiler.sprout`. Six C builtins in `runtime/sprout_runtime.c` (using `long long` throughout for bootstrap-compatible ABI; string params cast to `const char*` internally); declared as `extern fn` in `stdlib/compiler.sprout` — stage1-compatible without checker/codegen table entries. REPL migrated from `CompilerSession` to `StatefulSession`; instances/completion fall back to local mirrors. Branch: `sproutd-daemon-foundation`.
  - **sproutd daemon M4 — LSP layer (2026-05-26)**: `stdlib/compiler/lsp_driver.sprout` implements a minimal LSP 2.0 server (`run_lsp(stdlib_root)`). Uses `Content-Length: N\r\n\r\n` framing: writes with `term_write` (byte-length via `string.byte_length` backed by `str_byte_len`/`strlen`); reads headers with `term_read_line` then reads the body with `stdin_read_bytes(n)` (new builtin: `fread` of exactly N bytes, required since LSP bodies are not newline-terminated). `--lsp <stdlib_root>` mode calls `sproutd_init_with_root(stdlib_root)` (new C builtin) to wire `SPROUT_ANALYSIS_SERVICE_CMD` using the explicit CLI argument rather than inferring from exe-path. `LspState` holds open documents (`Ref(Dict String)`) and shutdown flag. Handles: `initialize` (capabilities: full-sync, hover, stub completion), `shutdown`/`exit`, `textDocument/didOpen/didChange/didClose` (stores source, pushes diagnostics via `compiler.diagnostics_in_source`), `textDocument/hover` (extracts word at cursor, calls `compiler.type_of_in_source`), `textDocument/completion` (stub: empty list). Uses **public** `stdlib/compiler.sprout` API only to avoid a self-hosted type qualification bug. Known gaps (BACKLOG items): diagnostic positions are always the full-range first error only; completion is a stub. Branch: `sproutd-daemon-foundation`.
  - **sproutd daemon M3 — combined binary + self-sufficiency (2026-05-26)**: Single `build/sproutd` binary replaces both `repl_bin` and `analysis_service_bin`. When invoked with `--analysis-service <stdlib_root>` runs as the service; otherwise runs as the REPL. `sproutd_self_init()` C builtin auto-sets `SPROUT_ANALYSIS_SERVICE_CMD` and `SPROUT_DARWIN_FRAMEWORKS` at startup by resolving the executable's own path via `_NSGetExecutablePath`/`realpath` (macOS) or `/proc/self/exe` (Linux) and inferring stdlib root as `<exe_dir>/../stdlib` (overridable via `SPROUT_STDLIB_ROOT`). `analysis_service_driver.sprout` module renamed from `module main` to `module stdlib.compiler.analysis_service_driver` to avoid LLVM `main` symbol conflict when imported by `sproutd_driver.sprout`; thin `analysis_service_main.sprout` entry point created. `repl.sprout` exports `run()` (was `main()`) to avoid same conflict. `examples/repl_hosted.sprout` gets its own `fn main() = repl.run()`. `justfile` gains `build-sproutd` recipe; `repl` recipe updated to use `build/sproutd` directly. `SPROUT_ANALYSIS_SERVICE_CMD` env var still overrides auto-discovery. Branch: `sproutd-daemon-foundation`.

### 7.2) Doc-sweep follow-ups (2026-08-18)

Found while correcting stale claims across the user-facing docs; neither is a doc fix.

- [ ] `P3` **`--emit-ir --debug` is inert at the driver.** Both arms of the `--emit-ir`
  match in `compile_driver.main` dispatch to `run_batch(..., "ir-typed", ...)` identically
  — the flag only shifts the argv start index, and nothing downstream reads it. The DWARF
  that `just build-debug` produces comes entirely from `clang -g -O0`. `docs/debugging.md`
  §"Debugging compiled programs" already records this ("the `--debug` flag is currently a
  no-op … a re-addable follow-up"), and `docs/development.md` now says so too. Either emit
  Sprout source-level DWARF from the typed IR pipeline, or drop the flag so the CLI stops
  advertising a capability it does not have.
- [ ] `P3` **`docs/int-overflow-policy-decision.md` §2 cites `stdlib/compiler/codegen.sprout:2106`**,
  deleted 2026-07-12. That doc is a live decision document — `docs/spec-v0.md` §8.4 now points
  readers at it for the open `+`/`-`/`*` policy question — so its ground-truth section should
  re-verify against `ir_lowering.sprout` rather than a file that no longer exists. Cheap: the
  finding itself (plain `add/sub/mul i64`, no `nsw`) is still correct, only the citation rotted.

### 7.3) In-Language Stdlib Test Framework

- [x] `P1` Add `stdlib/test.sprout`: minimal HUnit-style framework (`TestState (Ref Int) (Ref Int)`, `new_state`, `assert_true`, `assert_false`, `assert_eq`, `summary`). `assert_eq` uses `where Eq a, ToString a` — correct class-based equality, `ToString` only for failure messages.
- [x] `P1` Add `Eq (Maybe a) where Eq a`, `Eq (List a) where Eq a`, `Eq (Result e a) where Eq e, Eq a` instances to `stdlib/prelude.sprout`. `Eq (List a)` uses top-level `list_eq` helper to avoid self-referential instance-body `eq` call.
- [x] `P1` Add `Eq Type`, `Eq Effect`, `ToString Type`, `ToString Effect` instances to `stdlib/compiler/types.sprout`. `Eq Type`'s `TTuple` case uses `types_eq` helper for the same reason.
- [x] `P1` Add `tests/stdlib/test_math.spr` (27 assertions), `tests/stdlib/test_string.spr` (31 assertions), `tests/stdlib/compiler/test_types.spr` (20 assertions).
- [x] `P1` Fix stage-1 parameterized instance inner-dict crash (2026-05-21): `instance Eq (Maybe a) where Eq a` called with concrete `Maybe Int` caused SIGSEGV because the `Eq Int` inner dict was not captured in the method closure. Two-part fix: (1) `infer.sprout` TDict nodes now store the full concrete `TypeApply(Maybe, Int)` in `type_args` instead of just `TypeName("Maybe")` — enables unification in lowering; (2) `lowering.sprout` `resolve_tdict_for_inst` unifies instance type-args against call-site type-args to derive a substitution `{a → Int}`, then builds a lambda wrapper `\l r -> __tc_Eq_Maybe_a_eq(l, r, __tc_Eq_Int_eq)` for each method. Also fixes `parser.sprout` double-reverse on constraint list in `parse_fn_decl`. test_math.spr (27/27), test_string.spr (31/31), test_types.spr (20/20) now pass at stage-1.
- [x] `P1` Add `test-stdlib` recipe to `justfile`: pure-shell loop over `tests/stdlib/` and `tests/stdlib/compiler/`, greps for `SUITE FAILED`, exits 1 if any suite fails. No Python.
- [x] `P2` Add `instance Eq (Vec a) where Eq a`, `instance Eq (Dict v) where Eq v`, `instance ToString (Vec a) where ToString a`, `instance ToString (Dict v) where ToString v` to `stdlib/prelude.sprout` (2026-05-24). `Eq Vec` uses `vec_eq_from` (indexed recursive helper via `vec_get`). `Eq Dict` uses parallel traversal via `dict_entries` — mirrors Haskell's `Data.Map` approach (`toAscList` comparison), correct because the underlying BST is keyed by `strcmp` so entries are always in sorted lexicographic order. `ToString Vec` mirrors `ToString (List a)` style; `ToString Dict` renders as `{key: value, ...}`.
- [x] `P2` Add `tests/stdlib/test_collections.spr` (13 assertions): replaces element-extraction workarounds with direct `assert_eq` on `Vec Int` and `Dict Int` values including structural equality checks on `vec_reverse`, `vec_slice`, and `dict_remove` results (2026-05-24).
- [x] `P2` Add law-oriented conformance tests via `assert_eq` once list/functor/monoid `Eq` instances are stable.
- [x] `P1` **Gate the orphaned conformance corpora.** Audit (2026-07-27) found only `conformance/type_error` (via `test-type-errors`) and `package_resolution` were wired into any gate; `conformance/run` (golden stdout, 26 fixtures — grown as recently as records-v0 4 days prior), `parse_error`, `runtime_error`, `executable_error`, and `parity_*` ran **nowhere**. Symptoms of the rot: an orphan `run/stdlib_fold_filter_map.out` whose `.spr` was deleted in the W8-totality commit and never noticed; the landed W5-exhaustiveness feature's "positive conformance" fixtures (`run/{exhaustive_match_shapes,tuple_catchall_reachable}`) had never actually executed in CI. Fix: new `test-conformance-run` recipe (compile → link → run → byte-diff stdout vs `.out`; detects the `--emit-ir` "ERROR:-into-stdout, exit 0" blind spot by grepping output, not exit status) with a `tests/conformance/run/XFAIL` manifest that quarantines known-broken fixtures **visibly** — an unexpected pass or an orphan `.out` fails the gate (self-healing). Generalized `_test-type-errors` → `_test-reject <dir>` and added `test-parse-errors`. Both wired into `ci-fast-gates` and `test`. Result: 18 `run/` fixtures now gated green, 8 quarantined with per-fixture reasons.
- [ ] `P2` **Rehabilitate the 8 quarantined `run/` fixtures** (`tests/conformance/run/XFAIL`). All 8 reference prelude names without importing — bare `.spr` files are self-contained and get no prelude. Four (`stdlib_laws_functor`, `stdlib_laws_monoid`, `stdlib_string_template_{basic,callsite}`) additionally use the **removed** `Cons`/`Nil` cons-list type → rewrite to `Vec`/`[...]`. Two (`stdlib_mixed_io_{maybe,result}_do`) need a prelude import for `Just`/`Ok`. Two (`aoc2025_day1_sample` `>>`→`@lcompose`, `stdlib_two_constraints_same_class` `@Err`) fail at link on an undefined prelude symbol — confirm whether an import fixes them or they expose a real codegen gap. Un-quarantine each as it is repaired (the gate flips red if a repair works but the basename stays in XFAIL).
- [~] `P2` **Dispose of the obsolete negative corpora.** Chose option (b): re-implement entrypoint validation. **LANDED (`validate_entrypoint`, `infer.sprout`):** a `main` that is *defined* must be a well-formed entrypoint — zero args, `Unit`/`Int` return, concrete `!{IO}` effect (not pure, not effect-polymorphic). Runs as a final gate on an otherwise-valid program (after the body typechecks, so it never masks a real body error). The 5 signature fixtures in `conformance/executable_error` are revived and gated via `test-executable-errors` (`_test-reject`, wired into `ci-fast-gates` + `test`); `.err` strings updated; positive fixture `conformance/run/main_int_exit_ok.spr` added. Spec §10.10 already specified these rules (previously unenforced) — now enforced.
  - **Deferred: the "missing main" check** (`executable_error/missing_main` is xfailed). Requiring a `main` cannot be a type-check error — at type-check the compiler can't tell a library check (`--phase check`, no `main` expected) from an executable build, and main-less library files are legitimate (e.g. `examples/sentry_api.sprout`, `examples/sentry_issue_browser_tui.sprout`). It needs an explicit executable-vs-library compile mode (thread the intent from `--emit-ir`/codegen, where `has_user_main` is already computed) before it can fire. `runtime_error/main_arity_mismatch` (a duplicate of the arity case, mislabeled as runtime) and `parity_*` (byte-parity vs the retired reference, no golden) are still candidates to delete — decide separately.
- [~] `P2` **Test-assertion boilerplate: pure-list `check_eq`/`run_suite` redesign.** The old framework threads a `TestState` `Ref` through every assertion — ~2,560 call sites across 222 files each repeat `state`, plus per-file `new_state`/`do`/`summary` scaffolding. **API LANDED:** `stdlib/test.sprout` now exports `check_eq(label, actual, expected) where Eq a, ToString a -> TestCase` (evaluates `eq`+`to_string` eagerly → a *monomorphic* `TestCase(label, passed, detail)`, so a heterogeneous set of assertions collapses into a homogeneous `Vec TestCase`), `check_true`/`check_false`, and `run_suite(name, cases)` which folds the Vec *purely* and prints one report + the `SUITE PASSED/FAILED` line the runner greps (no `panic`, so no flush dependency). HUnit/tasty value-model adapted to a strict language (Rust `#[test]` discovery is off the table without macros/reflection). The effectful `assert_eq`/`state` API is retained as the escape hatch for tests that need IO to produce the actual (e.g. `test_log.spr` capture sink, ~21 IO files). **Migrated: 89 files** — 3 pilot (`test_math`/`test_applicative`/`test_string`) + 86 via a 5-way Sonnet codemod (854 assertions, count-verified 1:1, full `just test` + `test-stress` green). Two more codemod outputs (`test_list_pattern_runtime`, `test_vec_literal_coercion`) were reverted to the old API: their *helper* functions carry pre-existing, deliberate lint-flagged content (a legacy `Cons x Nil` parity test; a `vec_from_list([...])` that is literally the `redundant-vec-from-list` rule's own target), and the pre-commit lint hook blocks re-staging them. The linter has no inline suppression mechanism, so intentional lint-rule-target test content cannot be re-committed — see the follow-up item below. **Staying on the effectful `TestState` API by design:** ~79 files with interleaved IO binds (channels, tasks, `Ref` sinks, compiler AST-building) + ~51 files whose `main` interleaves order-sensitive `let`/`match`/GC-churn between assertions (the sequential `do` form is clearer and, for GC-stress tests, order is the point). That split is the intended end state, not remaining debt — the pure-list API is for pure-assertion suites, the effectful API for order/IO-sensitive ones. **Codemod lessons** (for any future batch): Sprout list literals reject a trailing comma after the last element (hard parse error); pure `let`s threaded through `main` hoist cleanly to a `let … in run_suite(...)` prefix (backward-only deps, order-independent for pure values); a branching `main` becomes `run_suite(name, match … with | … -> [checks])` returning a `Vec TestCase` per arm; `let … in` cannot bind a multi-line `match` RHS (use a `where` clause instead). Design decision recorded: eager `check_eq` trades per-assertion crash localization, but that is already near-illusory (the harness captures stdout block-buffered, so a SIGSEGV discards buffered `PASS:` lines today); the flush item below would restore it for both APIs.
- [ ] `P3` **Linter has no inline-suppression mechanism.** `fmt_bin lint` (`stdlib/compiler/lint_rules.sprout`) offers no per-line/per-file opt-out (no `# lint: allow <rule>` convention), so deliberate lint-rule-*target* content — e.g. a test that exercises the exact `Cons x Nil` fixed-length match or the redundant `vec_from_list([...])` wrap the rules discourage — cannot be re-staged once the pre-commit hook runs (it lints staged `.spr` files). Surfaced migrating `test_list_pattern_runtime`/`test_vec_literal_coercion`. Note lint is *only* a pre-commit gate (not in CI), so such files sit on master until re-touched. Add a suppression comment convention (Rust `#[allow]` / ESLint `// eslint-disable-line` style) and honor it in the rule walker.
- [x] `P3` **`gate-audit` false-flags `ci-fast-gates`. ALREADY FIXED — verified 2026-08-07.** The prescribed fix (add `ci-fast-gates` to `gate-audit`'s `EXCLUDE`) is present in the justfile with a written rationale, and `gate-audit` now runs green inside `ci-fast-gates` itself, so it is no longer latent either. Note the new assertion C added 2026-08-07 approaches the same problem from the other side and does *not* need the exclusion: rather than name-matching, it reads `ci-fast-gates`' `GATES` array and expands each entry's dependency closure, so an umbrella recipe covers its children without special-casing.
- [ ] `P2` **Robust suite pass/fail signalling + stdout flush.** Today the runner derives exit status by grepping stdout for `^SUITE FAILED` — couples the machine gate to a human string, false-trips on a fixture that prints it as data, and blocks golden-stdout tests from sharing the stream. Replace with a real nonzero exit via the existing `panic` extern (`panic(msg) -> a !{IO}`, already `exit(1)`s — no new builtin) when `summary`/`run_suite` sees a failure. Separately, `print` is `printf("%s\n", …)` with no `fflush`/`setvbuf`, so output is lost on a crash under capture; add `setvbuf(stdout, NULL, _IOLBF, 0)` at runtime startup (or `fflush` in the print builtin) so crash output survives and per-case localization becomes real. The runtime edit touches `runtime/sprout_runtime.c` → needs approval per the builtin/runtime rules.

### 7.5) Type Classes (Collections First)

- [x] `P0` Add class declarations and constrained function signatures (`class`, `where` constraints).
- [~] `P0` Add instance declarations and resolution (`instance` lookup, coherence checks). **Audited 2026-08-15** (probe sweep against a stage-1 build): declaration + resolution work, including across module boundaries, and the overlap check fires on both same-module and imported-instance conflicts — imports are inlined by `bundler.collect_modules` into the one `decls` list `check_overlapping_instances` scans, so coherence is bundle-global *on the compile path*. **Not verified:** the REPL / LSP / analysis-service path, which per `infer.sprout:4778` supplies imported names as env schemes rather than decls, and so may not see imported instances' `InstanceDecl`s at all — see the new item below. The marker-borrowing incoherence formerly documented at `infer.sprout:1487` is fixed and covered (`ambiguous_class_tyvar_incoherent`). One defect found and FIXED in the same pass: a bare type-variable instance head (`instance C a`) was accepted at the declaration and then dead at every use — it registered `@inst:C:a` (the head keys on `type_head_str`, and `type_from_ast` at that site has an empty `local_vars`, so the variable resolves to `TConst("a")`) — a key dispatch never looks up, since no real type is spelled lowercase. Each use therefore failed `No instance of C for T`, blaming the caller. Now rejected at the instance site (`check_tyvar_instance_heads`); spec §8.5. Code review then found the first cut leaked through two `TypeExpr` forms that also reduce to a variable head — `instance C (a !{IO})` (effect annotations are dropped by `type_from_ast`) and `instance C (a -> b)` (no constructor head at all) — both now rejected; fixtures `tyvar_instance_head{,_applied,_effect}` and `instance_head_function_type`.
- [x] `P0` **Instance selection keys on the head constructor alone. FIXED 2026-08-15 by restricting the head (Haskell 2010 §4.3.2).** Filed as `P2` "disjoint parametric instances collide" — `instance C (List Int)` and `instance C (List Bool)` rejected as overlapping though no type matches both. That over-approximation was real but was the *lesser* half. Probing the single-instance case found the mirror image, and it is a **soundness hole**: with only `instance Describe (List Int)` declared (so the overlap check cannot fire), `describe` on a `List String` was ACCEPTED — `--phase check` OK, clean IR, clang linked, and the run printed `8737893664`, two String heap pointers summed as machine integers by the instance body's `Int` arithmetic. Root cause of both: `register_instance_marker` keys via `type_head_str`, whose `TApp base _` arm discards every argument, and `lowering.collect_instance_table` mirrors it — so `List Int` and `List String` are one key and nothing ever compares an instance's arguments to the call site's.
  **Fix (option A of the 2026-08-15 decision):** reject the head instead of widening the key. An instance head must now be a type constructor applied to *distinct type variables* — `head_arg_problem` + `instance_head_args` in `infer.sprout`, alongside the existing head-shape check. Concrete arguments, repeated variables and nested arguments are rejected where they are written. This makes the coarse key honest: every head it can represent, it now represents exactly. Fixtures `instance_head_{concrete_arg,repeated_tyvar,compound_arg}`; spec §8.5 rewritten (rule, the three new diagnostics, the newtype idiom, and why FlexibleInstances and full-head matching arrive together). Tree impact: no stdlib instance affected (all ~68 already have simple heads); `tests/conformance/type_error/overlapping_instance.spr` rewritten to collide via `List a` + `List b` (its old `List Int` now trips the head check first); `examples/typeclass_collections_demo.sprout` rewritten around newtype wrappers, since it was built on `instance Summable (Vec Int)` — i.e. it was a user-facing demo of the unsound feature.
- [ ] `P2` **Widen the `@inst:` key to full-head matching (GHC `FlexibleInstances` position).** Would make `instance C (List Int)` and `instance C (List Bool)` both legal *and* sound, lifting the restriction above. Deliberately deferred: exact-string widening does not work (`instance Eq (Maybe a)` keys `Maybe a` while a call site at `Maybe Int` looks up `Maybe Int` and misses), so it means unifying against stored heads with a most-specific-wins rule — instance-specificity *semantics*, not a representation swap, and therefore the Design Change Process. Measured blast radius: 13 `@inst:` sites in `infer.sprout` (10 `dict_get` reads), 2 in `resolve.sprout`, both key writers (`register_instance_marker`, `instance_key_and_class`), lowering's parallel `instance_table` (`constraint_key_from_tc`), plus seed refresh and golden IR. Prior art: GHC pairs `FlexibleInstances` ("allows the head … to mention arbitrary nested types") with matching "every instance declaration against the constraint, by instantiating the head" — the relaxation and full-head matching arrive together.
- [ ] `P2` **Instance overlap is not enforced against env-supplied instances on the REPL / analysis-service path. CONFIRMED 2026-08-15** (was "untested"; probed via `compiler.compile_source_with_cache`, the exact path the REPL uses).

  | probe | REPL path | compile path |
  |---|---|---|
  | session redeclares a PRELUDE instance (`instance ToString Int`) | **ACCEPTED** | rejected |
  | duplicate within the session's OWN decls | rejected ✓ | rejected |
  | tyvar instance head | rejected ✓ | rejected |

  The check *is* running on this path — the second row proves it. It is specifically blind to **env-supplied** instances: `checker.check_program_with_env` passes imports and the prelude as `extra` env schemes (so their instances arrive as `@inst:` env markers), while `check_overlapping_instances` scans `decls`, which here holds only the session's own source. **`check_tyvar_instance_heads` is unaffected** — a variable head is a property of the declaration alone and needs no cross-module knowledge, so it fires correctly on both paths.

  **Open question that sets the severity, deliberately not chased:** which instance wins at dispatch when a session redeclares `ToString Int`. Needs evaluation through the analysis service, not just a compile. If the session's instance wins, session shadowing is already the de facto semantics and mostly needs *specifying*; if the prelude's wins, the session instance is silently dead — the same class of defect as the tyvar instance head fixed in PR #108/#109, and it clearly wants rejecting.

  **Decision required before implementing** (Design Change Process — this is a semantics question, not a patch): either make the REPL reject the redeclaration for consistency with `--phase check`, at the cost of an affordance most REPLs offer, or *define* session-level instance shadowing and write it into the spec. What exists today is neither: an undeclared path difference where the same source is accepted interactively and rejected by the compiler.
- [~] `P0` Implement dictionary-passing lowering in typechecker/backend (hidden-method-parameter lowering supports constrained polymorphic helpers via forwarding and now monomorphizes concrete call sites to specialized wrappers; true first-class dictionaries for polymorphic class methods are blocked on higher-rank method-field representation). **Audited 2026-08-15** — the entry is accurate. Confirmed working: a class method in value position at a concrete type, and a method value whose dictionary comes from the *caller's* constraint (`fn describe(v: a) -> String where Show a = apply_it(show, v)` dispatches correctly per instantiation). Confirmed blocked, and the blocker is at the **syntax layer before the representation layer**: a method-level constraint inside a class is a parse error (`Expected }`), and there is no `forall` surface at all (`fn f(g: forall b. b -> String, …)` fails with ``unknown type `b.` ``). So this needs a parser/surface change first. **Same root cause as the `Traversable` entry below**, which cites the identical `Expected }` failure — one surface change unblocks both; sequence them together.
- [x] `P0` Add `Functor` class and instances for `List` and `Vec`.
- [x] `P0` Add `Foldable` class and instances for `List` and `Vec`.
- [x] `P1` **Add `Applicative` class (map2-based, Package C-b) + `map3`..`map5` + `Maybe`/`Result`/`List` instances.** Class `Applicative f where Functor f { pure, map2 }`; the primitive is `map2` (fully-saturated n-ary lift), **not** `ap`/`<*>` — `ap` feeds one wrapped arg at a time, needing the curried partial application C-b removed (spec §5.3). `pure` dispatches on return type (Enum-style). Instances: `Maybe`/`Result e` fail-fast, `List` cartesian product. `map3`..`map5` are free functions deriving from `map2` by tuple-threading (no currying). Landed with `tests/stdlib/test_applicative.spr`; spec §8.5. Closes the original "add Applicative to Sprout" goal for the core; follow-ups below.
- [x] `P1` **Add `Monad` class (`flat_map`) + generic `and_then` + `Maybe`/`Result e`/`List` instances; `maybe_with_default`/`maybe_or_else` free fns.** Completes the Functor→Applicative→Monad tower. `class Monad m where Applicative m { flat_map(f: a -> m b, xs: m a) -> m b }`; `and_then` is the exported free fn (delegates to `flat_map`, mirroring `fmap`/`map`). Dispatch keys on `xs`, so no return-type annotation needed (unlike `pure`). **No `Alternative` class**: the only lawful `List` instance is `++` (duplicates `Semigroup (List a)`), so with List excluded it'd be single-instance — `or_else` shipped as a `Maybe` free fn instead. Prior-art survey (Haskell/Cats/PureScript, verified) + lawfulness argument (left-biased List breaks MonadPlus left-distribution): `docs/monad-alternative-v0.md`; spec §8.5. Landed 2026-07-27 (`test_monad.spr` + Monad laws in `test_typeclass_laws.spr`). Follow-ups below.
- [ ] `P3` **`Alternative` class + generic `or_else`** — deferred until a *second* lawful instance exists (e.g. a parser combinator type); with only `Maybe` it is single-instance ceremony. `List`'s lawful instance (`++`) is already `Semigroup`.
- [ ] `P3` **Monad-generic `do` + built-in `?` propagation** — wire the `Monad` class into `do`/`<-` (currently desugarer-special-cased for `Maybe`/`Result`) and add the Tier-2/3 propagate form. See `docs/let-else-and-monadic-binding-plan.md`; this is the rung that flattens the `staircase-of-doom` error cascades.
- [ ] `P3` **Adoption sweep for the new combinators** — replace the ~103 hand-rolled `Nothing -> Nothing`/`Just x -> match…` bind chains (80% in `stdlib/compiler/`) with `and_then`, and the `with_default`/`or_else`/`map` parity sites, staged; compiler edits cost a reseed so batch carefully. Skip the trivial `maybe_map` sites (generic `map` already covers them — an adoption, not a bug). **Trap:** `maybe_or_else(primary, fallback)` is *strict* — `fallback` is always evaluated. Several orElse census sites put a real lookup in the Nothing arm (`Nothing -> attr_value(rest, key)`, `-> json_get_field(...)`); a naive swap changes "evaluate only on Nothing" to "always evaluate," so those need a thunk or must stay as-is. **Closure-cost trap (verified 2026-07-27, reverted an iface_codec batch over it):** `and_then(\x -> body_capturing_locals, scrut)` allocates a **heap closure per call** when the lambda captures enclosing locals — which nearly every real bind arm does (e.g. `\j -> collect_x(s, bytes, skip_ws(s,bytes,j,total), total, Nil)`). The original `| Just x -> …` match arm allocates nothing. So `and_then` is only a *net* win for **point-free** arms `| Just x -> namedfn(x)` → `and_then(namedfn, scrut)` (passes a static fn pointer, no alloc). Census win-set after this filter is tiny (~4 sites: `lsp_driver.parse_content_length`, `desugar_ctx.type_expr_bare_name`, `lsp_driver.json_get_str`/`json_get_int_fld`) — NOT worth a compiler reseed. **Do NOT convert capturing-lambda arms, and never on hot paths (infer/codegen/lowering).** Also: these are depth-1 2-arm matches, not the depth≥3 `staircase-of-doom` the lint targets. Net: the compiler `and_then` sweep is largely net-negative; treat as closed unless the point-free sites are bundled into unrelated edits to those files.
- [ ] `P2` **`Validation` type + error-accumulating `Applicative` instance** — the killer app (form-style validation collecting *all* errors, not fail-fast). Needs its own type (`Valid a | Invalid e`) because a type admits one `Applicative` instance and `Result`'s is fail-fast; the instance requires `Semigroup e` to combine errors. Deferred from the minimal core slice (Kuba's scope call).
- [ ] `P2` **`traverse`/`sequence`** — deferred with `Validation`. Shape decided empirically: a `Traversable` *class* is **blocked** — the parser rejects a method-level `where Applicative f` constraint (a fresh method tyvar distinct from the class variable; "Expected `}`"). So ship them first as **free functions** (`list_traverse`/`list_sequence`, `where Applicative f`, structure hardcoded — the `concat_map` pattern; verified to compile+run). A real `Traversable` class needs parser support for method-level constraints (own item).
- [ ] `P3` **`ZipList` newtype** — the pairwise (`zip`-style) `Applicative` for lists, distinct from the prelude `List` instance's cartesian product. A `wrap ZipList a = List a` with its own `map2`/`pure` (`pure` = infinite/repeat semantics need a bounded variant).
- [ ] `P2` **Nested return-type dispatch: `map2(g, pure(x), pure(y))` miscodegens when `f` is fixed only by context** (verified 2026-07-27). When an Applicative method's argument is itself a return-type-dispatched call (`pure`), and the concrete `f` is determined only by the surrounding context (the RHS of an `assert_eq`, a return annotation) rather than by a concrete argument, codegen emits an **undefined `@map2`** (the unselected class method) → link error `use of undefined value '@map2'`. The checker types the expression fine; only instance *selection* fails to propagate from the context/result anchor through `pure`'s own dispatch into `map2`'s argument position. Workaround (used in `test_typeclass_laws.spr`): a typed intermediate — `fn m_pure(x: Int) -> Maybe Int = pure(x)` — makes `map2`'s argument concrete. Likely the same family as the return-first/input-later dict-mapping fix (PR #217), but nested (one return-dispatched call feeding another). Fix: during dispatch resolution, propagate the resolved result-type instance to nested return-dispatched arguments.
- [x] `P1` Add `Semigroup` class with associativity law documented.
- [~] `P1` Replace `++` special-case dispatch with proper infix operator machinery: `++` desugars to `append` in both Python and Sprout parsers; `__append` sentinel and `infer_semigroup_append` removed from `infer.sprout`; instance resolution uses head-constructor matching so `List Int` matches `instance Semigroup (List a)`; parser parity divergence eliminated. **REOPENED 2026-07-12:** only the parser/infer half landed. The active codegen (`ast_to_ir.sprout` `translate_append_operands`) still hardcodes `String`/`List` and emits `IRConst 0` (null) for every other concrete `Semigroup` type, so `dict ++ dict` and `vec ++ vec` silently miscompile to null and crash despite the checker resolving the correct `__tc_Semigroup_<T>_append` witness. Fix on branch `semigroup-append-dispatch`: consume the discarded witness (route the fallthrough through the resolved instance), keep `String`/`List` peepholes.
- [x] `P1` Add `Monoid` class with identity law documented.
- [x] `P1` Add `Eq` class with `==`/`!=` constraint checking; superclass of `Ord`.
- [x] `P1` Add `deriving Eq` (and similar) for ADT structural equality. `deriving (Eq)` now fully works: the `==`/`!=` operators desugar to `Eq.eq` class dispatch at infer time for any non-primitive type (ADTs, `Maybe`, `List`, polymorphic). Shipped on `feat/deriving` 2026-06-10; `==` dispatch fix on `fix/eq-operator-adt-dispatch` 2026-06-12. v1 covers `Eq`, `Ord`, `ToString`. See `docs/deriving-v1-draft.md`.
- [ ] `P2` Route `<`/`<=`/`>`/`>=` on ADTs through `Ord` class dispatch — currently `check_compare` only allows `Int`/`Char` and errors for ADT types; same shape of fix as the `==` ADT dispatch (infer.sprout:check_compare). A first attempt landed on `fix/eq-operator-adt-dispatch` (commit `7c7e571`) but was REVERTED 2026-06-13 because replacing master's load-bearing implicit Int/Char unification with pure `apply_subst` inspection left polymorphic TVar operands unresolved in the shared `InferState`, cascading into unrelated inference (e.g. partial applications in user code: `let f = string.repeat("dupa")` was typed as `String` instead of `Int -> String`, breaking codegen and producing SIGSEGV at runtime). Master force-binds TVars to Int — which worked because master rejected polymorphic Ord entirely (no `where Ord a` function bodies existed to exercise the leak). The Ord dispatch addition introduced polymorphic `where Ord a` functions like `min_of` that force the question: when `<` operands are TVars, bind eagerly or dispatch lazily? Eager binding breaks polymorphic Ord (`min_of(Player, Player)` would have its `a` TVar bound to Int); lazy dispatch breaks partial-app inference. The two requirements are at odds at the operator site. Redesign needed: implement Ord operator dispatch as a POST-PASS analogous to `resolve_dispatch_typed_expr` (PR #18) that rewrites `<`/`<=`/`>`/`>=` AFTER substitution is finalized, so concrete types are visible at rewrite time. That sidesteps the TVar-at-operator-site question — by the post-pass, the type is either concrete (Int/Char/String → fast path) or genuinely polymorphic via a `where Ord a` constraint (dispatch unambiguous). Implementation hint: hook into the existing post-pass walker in `infer.sprout` near `resolve_dispatch_typed_expr`; emit a placeholder `TBinary` at `check_compare` for non-primitive operand types, and let the post-pass either retain it (concrete primitive) or rewrite to a `TCall` to `ord_lt`/etc (TVar with Ord constraint). Empirical context preserved in commit message of `7c7e571` (still on `fix/eq-operator-adt-dispatch` history before reset).
- [ ] `P2` Revisit `compare` return type — `class Ord a { fn compare(left: a, right: a) -> Int }` currently returns `Int` with sign convention (negative = less, zero = equal, positive = greater), following the OCaml/C/Java/`Comparable` tradition. Sprout's design family (statically-typed functional with ADTs and pattern matching as first-class — Haskell, Elm, PureScript, Rust) uses a tagged enum instead: `type Ordering = LT | EQ | GT`. Tradeoffs to evaluate before deciding: (a) **Type safety** — `Int` admits ~4B nonsensical values for a 3-state outcome; `compare(x, y) == 1` is a silent bug (the convention is the sign, not the value), but compiles cleanly. `Ordering` forces exhaustive pattern matching at use sites. (b) **Consistency with Sprout's own design** — the language already has `Maybe`, `Result`, ADTs explicitly to avoid magic-number conventions (Maybe over nullable, Result over error codes); then `compare` reintroduces one. Self-inconsistent signaling. (c) **Beginner ergonomics** — `match compare(x, y) with | GT -> …` self-documents; "negative means less" requires internalising a convention from C/Java lineage a beginner may not have seen. (d) **Performance** — `Int` compare is one `icmp`; `Ordering` (nullary 3-ctor ADT) potentially allocates a tag per call. Depends on whether nullary ADT ctors lower to compile-time tags in current codegen — **needs profiling before any decision**. Specialization/monomorphization passes would close the gap further. (e) **Composability** — lexicographic compare with `Int`: `match compare(a1, a2) with | 0 -> compare(b1, b2) | n -> n`. With `Ordering`: `match compare(a1, a2) with | EQ -> compare(b1, b2) | other -> other`. Same length, `Ordering` is more explicit and rules out accidental arithmetic. (f) **Migration cost** — touches `class Ord` declaration in `stdlib/prelude.sprout`, derived Ord emitter in `stdlib/compiler/deriving.sprout`, the four `ord_lt`/`ord_lte`/`ord_gt`/`ord_gte` helpers, every existing `Ord` instance (`Char`, `String`, all derived), every user call site doing `compare(...) < 0` arithmetic. Surfaced 2026-06-12 during the ADT-operator dispatch fix work (`fix/eq-operator-adt-dispatch`). Decision needed: (i) keep `Int`, (ii) migrate hard to `Ordering`, (iii) deprecation path (`compare_int` keeps old behavior, new `compare` returns `Ordering`). Before deciding, profile nullary-ADT-ctor return cost in the current runtime so (d) is empirical, not handwaved.
- [x] `P1` Add pragmatic utility classes (`Eq`, `Ord`, `ToString`) for collection-focused workflows.
- [x] `P1` Add law-oriented conformance tests (functor identity/composition, monoid identity/associativity): `tests/stdlib/test_typeclass_laws.spr` (30 assertions); added `instance Functor Maybe` to `stdlib/prelude.sprout`; uses concrete typed helpers `nil_int()`/`no_int()` to anchor `Eq` resolution (2026-05-25).
- [x] `P1` Add diagnostics for ambiguous/overlapping instance errors. **DONE (PR-3).** `check_overlapping_instances` pre-pass in `infer.sprout` (runs in `typecheck_decls` after `validate_all_decls`) rejects two instances whose `@inst:{Class}:{Head}` markers coincide — catching both literal duplicates (`instance Foo Int` twice) and head-overlap (`instance Bar (List a)` + `instance Bar (List Int)`) — with `Overlapping instances for {Class}`. Scans the program's own decls (not the seeded prelude env), so prelude instances (each a distinct head) never false-positive. Bundled in the same PR: do-block short-circuit-family conflict diagnostics — `infer_do_steps` now threads a `family` (Maybe/Result) fixed by the first `<-` bind and errors on a later bind or the final expression in a different family. All four `tests/conformance/type_error/{duplicate_instance,overlapping_instance,stdlib_mixed_do_bind_family_conflict,stdlib_mixed_do_wrong_final_family}` promoted off xfail; verified by full suite + stage-2→stage-3 self-compile (the compiler's own do-blocks/instances don't trip either check).
- [x] `P1` Close the env-marker-only constrained-instance discharge gap in `resolve.sprout` (residual hole from PR #110). **DOWNGRADED 2026-07-01 (investigated, not reachable).** The bundler bundles imported modules' decls topologically, so an imported constrained instance's `where` context is in resolve's decl table and the recursion fires across module boundaries — verified: `stdlib.reprobox` exporting `instance ToString (Box a) where ToString a`, applied as `to_string(mk_box(()))`, is cleanly rejected with `No instance of ToString for Unit`. The env-marker-only set is just builtins (all unconstrained → nothing to miss) and future `.iface` imports (not in the default source-compile path). Residual hole is theoretical, gated on the iface arc, and closable only when iface imports carry instance context. No code fix now; caveat downgraded in `docs/dict-resolution-north-star-plan-2026-06-30.md`.
- [ ] `P2` Investigate qualified imported-constructor access (observed during the above, low confidence). `import stdlib.foo as f` then `f.MkCtor(x)` gave `Unknown variable: f.MkCtor` for a parametric ADT `export type Box a (..) = | MkBox a`, while the *type* `f.Box` and functions `f.mk_box` resolved fine, and `json.JsonBool` (non-parametric `Json`) resolved. Unclear whether qualified constructor access is intended syntax at all (selective import + unqualified may be the sanctioned form) — confirm the intended mechanism before treating as a bug.
- [x] `P1` Extend `Ord` instance coverage to match `Eq`/`ToString` (tuples 2–5, `Maybe`, `List`, `Result`, `Vec`). **DONE (PR-2).** Added lexicographic `Ord` instances for tuples 2–5, `Maybe`, `List`, `Result`, `Vec`. Root-cause fix bundled in: reordered `Maybe` (`Nothing | Just`) and `List` (`Nil | Cons`) declarations so declaration/tag order == semantic order == derived order (matching Haskell/Rust/OCaml, which all declare the smaller constructor first). This makes hand-written prelude `Ord` and `deriving (Ord)` agree — no prelude-vs-derived inconsistency. Reorder verified safe: every tag dependency is name-resolved (runtime `find_ctor_tag_by_name`, compiler ctor-sig dict); confirmed by full suite + stage-2→stage-3 fixed point + golden IR regen (pure tag renumbering, 32 files).
- [x] `P2` Dedup the `strip_module_prefix` copies now that `string.substring_after_last` exists. **DONE (PR-4).** Removed all six compiler-local copies (checker/infer/unifier/lowering/field_kinds/codegen); all 42 call sites now use `string.after_last_dot(name)` — a dot-specific convenience added to `stdlib.string` over `substring_after_last(name, ".")`. Added the `stdlib.string` import to infer/unifier/field_kinds (the "avoid circular imports" comment was stale — `stdlib.string → stdlib.collections` has no back-edge to any compiler module). Verified by stage-2→stage-3 self-compile + full suite.
- [ ] `P2` **`wrap` instance lifting — reuse the base type's typeclass instances as the wrap type.** Operating on a `wrap Age = Int` is awkward: `age + 1` is a type error and users hand-write `match age with | Age n -> Age(n + 1)`; `spec-v0.md:343-344` currently forbids `wrap` from deriving at all. Allow `wrap Age = Int deriving (Num, Ord, ToString)` to generate `instance C Age` decls that unwrap → delegate to `C Int` → rewrap results typed at `Age`, **while `Age` stays distinct from `Int` (not interchangeable)**. This is Haskell's `GeneralizedNewtypeDeriving`, verified to preserve type distinctness — NOT a `wrap`↔base coercion (auto-wrap destroys mistake-prevention; auto-unwrap loses the wrap type; both = transparent `type alias`). Reuses the `deriving.sprout` emitter and the `check_overlapping_instances` pre-pass; requires a resolvable base instance in scope (else compile error naming the missing `C T`). Prior art (Haskell GND, Scala 3 opaque + extension methods, Rust newtype) all keep the type distinct; none use coercion. **Scope confirmed 2026-07-13:** operators with all-wrapped operands — `age1 + age2 : Age`, `name1 ++ name2 : Name`. No unwrap/coercion (passing an `Age` where `Int` is expected is out of scope). `++` falls out for free: it already desugars to `Semigroup.append` dispatch, so lifting `Semigroup` to `wrap Name = String` *is* the feature — route the append witness to the lifted instance (not the `String` peephole; cf. hardened `translate_append_operands`). Design + survey: `docs/coercions-and-literals-v1-draft.md` (Case B). Out of scope: mixed `age + 1` with a bare `Int` literal (needs numeric-literal polymorphism, separate).
- [ ] `P2` Add deriving/specialization follow-ups once core class system is stable.
- [~] `P1` Fix `__unresolved_*` typeclass dictionary sentinel leak (surfaced by M3 IR codegen path 2026-06-17). **UPDATE (PR #110):** the user-facing *symptom* — a SIGSEGV when a nested constrained dictionary is unsatisfiable (e.g. `to_string([()])`) — is now fixed at check time by `stdlib/compiler/resolve.sprout`, which rejects such programs with `No instance of X for Y` before codegen. The sentinel *mechanics* (single resolution path / Evidence rewrite that would make the null-fill structurally unreachable) remain parked as "M3b" — see `docs/dict-resolution-north-star-plan-2026-06-30.md` for the verified sentinel-flow map and why M4/M5/M3b were parked (M3b is pure dedup with zero behavior change; a standalone null-fill→error guard is a dead, untestable branch post-#110). Original analysis follows: `lowering.sprout` emits `TVar("__unresolved_<Class>__")` at three sites (resolve_tdict_with_key:1255, resolve_method_with_lambda:1302, resolve_method_var:1320) when typeclass dictionary resolution fails. Root cause: `infer.sprout:type_to_typeexpr_with_prog_vars` (line ~910) produces `TypeName("_")` for fresh TVars without a `@fwd:name:Class` marker in env; downstream `constraint_key_str("Class", [TypeName "_"])` yields the sentinel key `"Class__"`, and resolution fails. **Direct codegen silently masks the bug** via `emit_named_call:2588` falling back to `zero_val(...)`, emitting a null function pointer that would crash or produce wrong results at runtime — see the related `zero_val` follow-up below. **The new `--use-ir-codegen` path correctly errors** with `unbound variable '__unresolved_Eq__'`, surfacing the defect. Affects 3 tests: `test_deriving_eq_parametric.spr`, `test_deriving_to_string.spr`, `test_eq_operator_adt_dispatch.spr`. Fix scope: trace the specific call site producing `TypeName("_")` (candidates: polymorphic `equal_pair` invocations, inner-TVar resolution at `Just(1) == Just(1)`, derived `to_string` with parametric `++`), then ensure `@fwd:` markers are populated before `type_to_typeexpr_with_prog_vars` runs. Medium scope (~50-100 LOC in infer/lowering), moderate risk (touches fixed-point inference). Investigation report: `.claude/plans/` from the PR 16 investigation session.
- [x] `P1` Tighten direct codegen's silent `zero_val` fallbacks for unknown names. **CLOSED 2026-08-06 — MOOT: `stdlib/compiler/codegen.sprout` no longer exists.** Direct codegen was retired and the IR path (`ast_to_ir` → `ir_lowering`) is the only codegen path, so neither `emit_named_call` nor `emit_var` has a `zero_val` fallback to tighten. The IR path is strict by construction: an unknown name is rejected at check time (`ERROR: check: Unknown variable: <name>`) and `ast_to_ir` errors on `unbound variable` / `unknown constructor` rather than zero-filling — visible in the quarantined conformance xfails. The `__unresolved_*` dict sentinel case is unaffected (still handled by `ast_to_ir`'s explicit sentinel check, guarded by `test_unresolved_dict_nullfill`). *Historical scope, for anyone tracing the old references: the loud `panic("codegen: unresolved call to '<name>' …")` lived at `codegen.sprout:2601` and the silent value-position fallback at `codegen.sprout:1900`; both went away with the file.* **Fallout that outlived it:** the `loud-fail-smoke` gate still greps for that deleted panic's message — see the entry below.
      Original remaining scope, kept for context: `emit_var` (`codegen.sprout:1900`) silently zero-filled *any* unknown name in value position (consumer C3 in the sentinel-flow map, `docs/dict-resolution-north-star-plan-2026-06-30.md` appendix). Tightening it must preserve the one intentional case: `__unresolved_*` phantom free-tyvar dict sentinels are correctly null-filled (guarded by `test_unresolved_dict_nullfill`; see `ast_to_ir.sprout`'s explicit sentinel check for the pattern to mirror) — unknown non-sentinel names should become a hard error, matching `ast_to_ir.sprout`'s strict behavior. Option (a)-style loudness preferred per `feedback_no_half_measures`. Surfaced 2026-06-17 during M3 IR codegen typeclass dispatch investigation.
- [ ] `P2` Complete the M3b eta→single-authority collapse (blocked on tyvar canonicalization). **M3b-5 landed 2026-07-08: `resolve_tdict` DELETED** (`docs/dict-resolution-north-star-plan-2026-06-30.md` §M3b-5 (PR-B)). Lowering's `try_eta_in_class`/`try_eta_forwarded_without_class` remain a *second* resolution authority for ONE shape: a **polymorphic (type-variable-head) forwarded** value-position class method (e.g. `apply_any(x, to_string)` inside `fn f(x: a) ... where ToString a`). `resolve.method_ref_evidence` emits `EvUnresolved` for the non-concrete head, so lowering resolves it. Making resolve emit `EvForward` produces a key `ToString_<generalized-tyvar>` that misses `ctx_fwd`'s source-name key `ToString_a` — the [[project_typevar_identity_generalization_gap]] name-vs-generalized divergence. Durable fix = canonicalize tyvar identity in the dict resolver; then delete `try_eta_*` and add the deferred `TFunc` gate (prereq 1) + marker-miss test (prereq 2). Until then the nullary value-position case stays correct via `try_eta_in_class`'s existing `TFunc` gate → clean sentinel. **Fix-options menu for "canonicalize tyvar identity" (from `project_typevar_identity_generalization_gap`):** (a) co-generate the `@constrained_N` marker's var name WITH the generalized scheme so they never drift (smallest change); (b) don't rename on `generalize()` — keep source names in the scheme (then `scheme_vars` and markers coincide, the precise `Just` branch fires); (c) key constraints by a canonical positional/De-Bruijn index, not names. Any of the three retires the whole family of name-mismatch bypasses. (Item 4 below — "Canonical type-variable identity" — ultimately landed a (c)-flavored fix, positional scheme-var identity; this item's own `try_eta_*` collapse is the one place in the dict-resolution subsystem that fix didn't reach, since it lives on the lowering side, not `infer.sprout`'s marker/scheme machinery.)

### 7.6) Editor integration — LSP server and the JetBrains plugin

Arc plan: `docs/intellij-plugin-v0.md`. Server-side context and the corrected status of
what the LSP actually does: `docs/language-server-roadmap.md`.

- [x] `P1` **Nothing had ever exercised the LSP transport, and the server shipped two capabilities
  it did not implement. DONE 2026-08-17.** The pure helpers were already unit-tested
  (`tests/stdlib/compiler/test_lsp_driver.spr`), but no test and no recipe ran `--lsp`, so protocol
  behaviour was known only by hand-driving the binary. Added `scripts/lsp_smoke.sh`
  (`just lsp-smoke`, plus a non-blocking `lsp` CI job) driving the real binary with framed
  JSON-RPC, and extended the unit suite 22 → 29 (digits inside an identifier, cursor on the dot of
  a qualified name, the `col == length` boundary, and position 0,0 — the one legal hover position a
  truthiness-shaped guard would drop). What the first transport run established, correcting the
  repo's own notes: diagnostic
  positions were **already precise** (the roadmap's "full-range first-error only" was wrong — the
  real defect is `end == start`), and `compiler.type_of_in_source` **exists**, contradicting the
  in-file comment claiming hover was blocked on it. `initialize` advertised `hoverProvider: true`
  and a `completionProvider` while returning `null` and `[]`; both were **removed**, because an
  advertised-but-dead capability gives an editor a broken feature rather than an absent one. The
  gate asserts the implication *advertised ⇒ answers*, so it re-arms itself automatically as each
  handler lands.
- [x] `P1` **Sprout files were plain text in every JetBrains IDE. DONE 2026-08-17 (M1).** New Gradle/Kotlin
  subproject `editors/intellij/` (toolchain pinned in its own `mise.toml` — Java 21, which the platform
  has required since 2024.2, correcting this arc's initial JDK-17 assumption; Gradle 9.7). Ships file type
  (`.sprout`/`.spr`), a hand-written lexer, syntax highlighting, a colour-settings page, a `#` commenter,
  and brace matching, plus a **flat** `ParserDefinition` — one root node over the tokens — because the
  commenter and brace matcher need *some* PSI and a real Kotlin parser is a permanent non-goal (it would
  be a second authority on Sprout's syntax). Colour classification follows
  `tree-sitter-sprout/queries/highlights.scm`, extended with the three things a tree-sitter query over
  that grammar had no node for: `#` comments, `${…}` interpolation, float literals. Recipes
  `plugin-test`/`plugin-build`/`plugin-verify`/`plugin-run`; CI job `intellij-plugin` runs test+build
  (not verify — it downloads GBs of IDE per version). 25 lexer tests, the strongest of which lexes real
  `stdlib/` and `stdlib/compiler/` sources and asserts full coverage with no unclassifiable characters.
  `verifyPlugin`: **Compatible** against 2024.2/2024.3/2025.1/2025.2, all **Community** — the empirical
  form of "the language layer needs no paid API", which building against Community makes structural.
  Two findings worth keeping: the verifier rejects any plugin ID containing the word `intellij`
  (`TemplateWordInPluginId`) and `buildPlugin` + tests both accepted the bad ID, so the verifier belongs
  in the release path; and Kotlin raw strings interpret `${…}`, which silently collides with Sprout's own
  interpolation syntax in any embedded sample.
- [x] `P1` **LSP client layer for the JetBrains plugin. DONE 2026-08-17 (M3).** `sproutd --lsp` now starts
  from the IDE, so diagnostics from the real typechecker appear in RubyMine and its commercial siblings.
  Toolchain paths come from settings (Tools → Sprout) with a bounded walk-up autodetect for the pair that
  must travel together — a built `build/sproutd` and the `stdlib/` it was built against; half a checkout
  is deliberately *not* a hit, since a stale binary paired with someone else's stdlib gives confidently
  wrong diagnostics. Unconfigured projects get one balloon with a Configure action rather than a silently
  dead server. **The optional-dependency question is resolved** (`docs/intellij-plugin-v0.md` §5.2): the
  split works, but the plugin verifier CANNOT gate it — run against Community it reports the missing LSP
  package whether the split is intact or broken, and says so ("may be caused by absence of optional
  dependency"), so it fails the build for a benign reason. Replaced with `just plugin-split-check`
  (`scripts/plugin_lsp_split_check.sh`, in CI): every class referencing `com.intellij.platform.lsp` must
  live under `dev.sprout.intellij.lsp`, asserted against the shipped bytecode, refusing to pass vacuously
  in either direction. RED-verified. 33 plugin tests (25 lexer + 8 detection).
- [x] `P1` **Go-to-definition. DONE 2026-08-17.** Was the first thing a user hit ("Cannot find declaration
  to go to"), because nothing answered `textDocument/definition`. Three cases now resolve: a name declared
  in the open document, a qualified name followed into its providing module (`string.trim` lands on
  `stdlib/string.sprout`), and a bare name brought in by a selective import. Built on the existing
  authorities rather than a fresh walk over `Decl` — `ast.decl_value_scopes` for values (which already
  covers constructors and class methods) plus the new **`ast.decl_type_names`** for the type axis that
  authority explicitly sets aside; the analysis service's local copy of that match was deleted in favour
  of it. Position is `ast.decl_pos`, so **externs are navigable** — every `Decl` carries a `SourcePos`, and
  `collect_decl_locations` skipping externs was a choice, not a limit (`stdlib.bits` is nothing but
  externs, so skipping them would make the module unreachable). A constructor or class method resolves to
  its enclosing declaration; no finer position is recorded. Locals, parameters and pattern bindings stay
  **out of scope** — nothing records their positions, and guessing would move the cursor somewhere wrong,
  which is worse than declining; `null` is a correct answer and the editor says so plainly.
  `compiler.declaration_position` lives in the compiler layer, not the transport, per
  `language-server-roadmap.md` §4.3. Covered by `test_definition_lookup.spr` (13 assertions, one per
  declaration form, pinning that positions are ORIGINAL line numbers despite header stripping) and four
  `lsp-smoke` assertions driving the real server, including the cross-file case and a decline.
- [x] `P1` **Hover returned null unconditionally, so the editor showed no types. FIXED 2026-08-17.**
  `do_hover` discarded its arguments. It now answers with the inferred type of the word under the
  cursor: `Int` for a binding, `Int -> Int` for a function, `String -> String` for `string.trim`.
  **The API is `compiler.type_of_expr_in_source`, deliberately NOT `compiler.type_of_in_source`
  despite the name.** The latter is a `declare` into the C runtime that talks to a co-process
  launched as `sproutd --analysis-service <stdlib_root>` — a command line carrying **no package
  roots** — so hover through it would have failed on exactly the projects whose diagnostics work,
  showing a type checker that appears to disagree with itself inside one file. That co-process is
  persistent (forked once, holding a warm cache), so the objection is correctness, not spawn cost.
  The mechanism — append `let __repl_source_value = <expr>`, typecheck, read the sentinel's `Scheme`
  out of the env — had **two** copies in `analysis_service_driver` and hover needed a third, so it
  was promoted to `compiler.scheme_of_expr_in_source` and both callers now use it. The core returns
  the `Scheme` and rendering is a wrapper, because `eval_expr_in_source` branches on
  function/polymorphic/effectful to decide how to print. Failures are an ADT rather than a message,
  keeping the service's error strings and the REPL's `:type` output byte-identical. Covered by
  `test_expr_type_in_source.spr` (9 assertions incl. the sentinel collision, a package-root case, and
  a negative control) and four `lsp-smoke` assertions, one of which cannot pass under the builtin
  route and was written before the design was chosen for that reason.
- [ ] `P1` **Wire the remaining three LSP features whose compiler API already exists.** formatting
  (`formatter.format_source`), document symbols (`symbol_inventory_in_source`), completion
  (`complete_in_state` — REPL-line-shaped, so it wants the document line up to the cursor). Each goes
  in with its own capability; `lsp-smoke` asserts *advertised ⇒ answers* and skips absent
  capabilities, so the gate arms itself as they land. **Check each API for the hover trap**: an
  `analysis_*` name in `stdlib/compiler.sprout` is a builtin that crosses the analysis-service fork
  and therefore loses package roots, while the in-process compiler modules keep them.
- [ ] `P1` **The LSP re-checks cold on every request.** `check_and_push_diagnostics` and `do_hover`
  each build a fresh `ModuleCache`; `compile_source_with_cache_roots` already exists and wants a
  cache held in `LspState`. Needs an invalidation policy for `didChange` of an imported module, which
  is why it is not folded into the feature changes. Measured 2026-08-17: 0.30s wall for initialize +
  didOpen + didChange on a two-import file, and ~1s for a cold check of a compiler-sized file against
  tens of ms warm.
- [x] `P1` **The env typecheck path could not resolve package roots, so a multi-root project reported
  every imported name as unknown. FIXED 2026-08-17 (M2).** `module_loader.resolve_module_path(name,
  root, extra_roots)` was called **only** from `bundler.sprout`; the env path (`build_import_pairs` →
  `load_module`, reached via `compile_source_with_root`) called `module_name_to_path`, which knows only
  `stdlib.*` and bare names — and `--package-root` existed solely on the batch CLI. Verified against a
  live editor session: `import loam.gfx as gfx` produced **no** import diagnostic and then
  `check: Unknown variable: gfx.draw_frame` for every use, so uncharted-suns opened solid red.
  Same two-front-end split as the env-path retirement item, fixed the same way the module-surface arc
  fixes them: the env path now **calls the existing resolver** instead of answering the question a
  second, narrower way. Roots thread through the recursion, so a package module importing another one
  resolves too. `sproutd --lsp <root> --package-root <dir>` (repeatable, unlike `compile_driver`'s
  single fixed-position form) plus `compile_source_with_roots` / `compile_source_with_cache_roots`;
  the stdlib-only signatures stay and delegate, so ~40 existing call sites are untouched. Covered by
  `test_env_package_roots.spr` (6 assertions incl. a negative control that the default search path was
  NOT widened, and a warm-cache case where a threading mistake would hide) and two `lsp-smoke`
  assertions driving the real server. Fixture reused from the batch-CLI gate — same module, other
  front end, which is the point. **Retiring the env path (BACKLOG item on the analysis service)
  subsumes this**; until then the two paths at least share the resolver.
- [x] `P1` **Unimplemented LSP requests got no response at all. FIXED 2026-08-18.** `dispatch_lsp`
  ended in `else ()`, which silently dropped every unknown method — including JSON-RPC *requests*,
  which the spec requires be answered. Found by driving the server with a conversation shaped like a
  real IDE's instead of the minimal one the smoke gate sent: RubyMine issues `documentSymbol`,
  `semanticTokens/full`, `codeAction` and `foldingRange` on the first file it opens, and every one
  vanished without a reply, leaving the client waiting on a response that never came. The decision is
  now the pure, exported `unknown_method_response` (`id` present ⇒ `-32601` error; absent ⇒ silence),
  so it is unit-testable and the transport only performs the IO. Three `lsp-smoke` assertions plus
  three unit tests, including that a **string** id is echoed verbatim — a client matches replies to
  pending requests by id, so a wrong id is worse than none.
- [x] `P1` **The server could not say it was alive. FIXED 2026-08-18.** A `window/logMessage` on
  `initialize` now reports the stdlib root and the package roots actually in effect. Motivated by a
  real report: answering "is the server even running?" required a process listing, because nothing
  the server did reached the IDE log — every other language server in the IDE is visible there. It
  also surfaces the two things that are easy to get wrong and impossible to see from outside: the
  wrong stdlib, and missing package roots.
- [ ] `P2` **The plugin cannot auto-detect a toolchain for a project that is not a Sprout checkout.**
  `SproutSettings.detectFrom` walks up for `build/sproutd` **and** `stdlib/` together; `uncharted-suns`
  has neither, and nothing within six levels above it, so the game repo — Sprout's only real user —
  can never auto-configure. Settings → Tools → Sprout works, but the only signal that it is needed is
  one transient balloon. Options: honour `SPROUT_ROOT`, remember the last working toolchain across
  projects, or make the unconfigured state persistent rather than a balloon.
- [x] `P0` **The plugin's LSP layer never registered, so nothing worked. FIXED 2026-08-18.**
  `sprout-lsp.xml` declared `<platformLspServerSupportProvider>`, which under
  `defaultExtensionNs="com.intellij"` composes to `com.intellij.platformLspServerSupportProvider`
  — an extension point no IDE declares. The platform's real name, read from RubyMine's own
  `intellij.platform.lsp.xml`, is **`com.intellij.platform.lsp.serverSupportProvider`**, so the tag
  must be `<platform.lsp.serverSupportProvider>`. An extension registered against a non-existent
  extension point is silently never instantiated: `fileOpened` never ran, no server ever started,
  and there were no diagnostics, no hover and no navigation — while syntax highlighting kept
  working, because the language layer registers elsewhere in a different descriptor. That
  combination is what made it look like a definition bug.
  **Nothing in the build could catch it.** `verifyPlugin` passed against 8 IDE builds: an extension
  tag resolving to no extension point is not an API misuse. `plugin-split-check` verified the
  bytecode split, not registration. Two tests now cover it: `SproutLspDescriptorTest` (pins the tag
  as text; runs everywhere) and `SproutLspRegistrationTest` (asserts the provider is in the EP's
  extension list). Verified against a real RubyMine, where the EP then holds
  `dev.sprout.intellij.lsp.SproutLspServerSupportProvider` beside Tailwind's, Vue's and the
  TypeScript ones.
- [ ] `P2` **The registration test is inert on the default build platform.** IntelliJ IDEA Ultimate
  carries the LSP *API* — its bundled plugins register providers with it — but declares no
  `com.intellij.modules.lsp` module, in either 2024.2.5 or 2025.1.7.2 (checked in `product-info.json`
  and across every bundled XML descriptor). Our optional `<depends>` keys on that module, so the LSP
  descriptor cannot load there and `SproutLspRegistrationTest` can only report itself inert. Setting
  `SPROUT_IDE_HOME` to an installed IDE that provides it (RubyMine does) arms the assertion, but CI
  has no such IDE. Options: find the smallest downloadable IDE that declares the module, or check the
  module list of the verifier's IDEs directly.
- [ ] `P2` **Diagnostic ranges are zero-width.** `lsp_driver.diag_range` sets `end == start`, so
  clients get a caret rather than an underlined token. Widening to the offending token's extent is
  small; full multi-token spans need the span refactor in `language-server-roadmap.md` §5.1.
- [ ] `P3` **LSP4IJ for Community-edition IntelliJ IDEs.** The JetBrains LSP API is unavailable in
  IntelliJ IDEA open-source builds and Android Studio, so the plugin's LSP layer is paid-IDE only
  (highlighting works everywhere). LSP4IJ (Red Hat, EPL-2.0, 2024.2+) would close that gap at the
  cost of a third-party runtime dependency.

### 8) Runtime and FFI Foundations for Database Clients

- [x] `P0` Define a safer representation for external resource handles (currently `stdlib.net` wrapper ADTs; true opacity still depends on hidden constructors).
- [x] `P1` Add environment/config helpers such as `env_get(name) -> Maybe String`.
- [x] `P1` Define test support for integration-style IO programs that depend on external services.

### 9) Language/stdlib primitives surfaced by graphics

> The graphics/game engine + galaxy game were extracted to the **uncharted-suns** repo
> (2026-07-30); their roadmap now lives there. These are the pure language/stdlib items that
> graphics work surfaced but that belong to Sprout the language — kept here.

- [ ] `P2` Language-core wart: a `wrap` type used in a **user-defined function's type annotation across modules** does not canonicalize — `fn f(v: linalg.Vec3)` in user code sees `linalg.Vec3` as distinct from the value's `stdlib.linalg.Vec3` (Call type mismatch). Values flow fine into the defining module's own functions, so stdlib APIs work; only user-written helpers over imported wrap types break. Likely in the module-qualified-type-identity machinery (docs/module-qualified-type-identity-design-2026-07-10.md).
- [ ] `P2` Language-core: unbox small fixed-shape numeric records (`Vec3 {x,y,z}` as 3 raw f64s, not a heap pointer) — the ergonomic+fast path for individual small vectors. Additive on top of the tested flat-buffer foundation (`stdlib/linalg.sprout` `Vec3` slice landed; `Vec4`/`Mat4`/`Quat` pending).
- [ ] `P2` Native `Float` (f32) type + `Vector Float` unboxed path (mirrors `RepScalarDouble` in the B1 codegen gate). Doubles-everywhere is correct today; float32 earns its keep only when Sprout owns bulk GPU-bound buffers. Evidence-driven — decide by measured buffer/upload cost.
- [x] `P1` **Double→Int conversion. CLOSED 2026-08-14** — shipped as `math.to_int : Double -> Maybe Int` plus `to_int_or`, and the rounding family it composes with (`ceiling`/`truncate`/`round` joining the pre-existing `floor`, all `Double -> Double`). Design, prior-art survey and the two traps: `docs/double-to-int-v0.md`. **Not** the core runtime/prelude primitive this entry asked for, and deliberately not: pure Sprout over the existing `double_to_bits` intrinsic costs no builtin, no runtime symbol and no seed `declare`, and an `fptosi` extern would need the same range guards anyway since `fptosi` is poison on NaN and overflow. Three things worth carrying forward. (1) **The layers separate.** Rounding stays in `Double` and is total, so the rounding-mode question never reaches the conversion; `to_int` is the single partial function and answers the out-of-range question once. That is also how Rust arranges it. (2) **The range boundary is asymmetric** — `2^63 - 1` is not representable as a Double (spacing 1024 up there) so nothing at or above `2^63` converts, but `-2^63` is a power of two, is exact, and must be answered directly rather than through negate-and-re-sign. (3) **NaN cannot be folded into a range guard**: every comparison against NaN is false, so `t >= hi || t < lo` is false for NaN and falls through to convert garbage. Superseded the duplicate `P3` entry in §math. The **lossless float text encoding** this entry also mentioned is NOT unblocked by it — that blocker is wide-integer arithmetic (128-bit/bignum for Ryū/Grisu), as the §math entry records.
- [ ] `P3` Remove the dead C `json_parse` tree-parser (`runtime/sprout_runtime.c`, marked SUPERSEDED; the Sprout `stdlib.json.json_parse` replaced it). Extract the shared low-level helpers (`sprout_json_skip_ws`/`_parse_string`/`_parse_hex4`) still used by the by-key extractor, then delete `json_parse`/`json_parse_value`/`_array`/`_object`/`_number`/`_ok_result`/`_err_result`/`_reverse_*` and drop `json_parse` from `APPROVED_BUILTINS`.
- [ ] `P3` `deriving (Enum)` breadth — `values`/`enum_values`, and optionally `succ`/`pred`/`min_bound`/`max_bound`. Deferred from the Enum landing (2026-07-24); no in-tree consumer needs them yet. `values : List a` (all variants in declaration order) is the most-requested (Java `values()`, Kotlin `entries`, C# `Enum.GetValues`, Scala 3 `values`) and rides the same return-type-dispatch path as `from_ordinal`. Ref: spec §8.6, `docs/deriving-v1-draft.md`.

### 10) Windows port and cross-platform runtime

> Design, milestone breakdown and the verified prior-art survey live in
> [docs/windows-port-v0.md](docs/windows-port-v0.md). Items below are the execution units; the
> doc is the source of truth for *why* each one is shaped the way it is.

- [ ] `P1` **Windows Milestone A — compile *to* Windows** (2026-08-15). Umbrella. **PARKED after W2 (2026-08-16), deliberately — not blocked, not half-landed.** W0a/W0b/W1/W2 are all on master, no branch is outstanding, every gate is green. `docs/windows-port-v0.md` §5.1 is the resume point: what compiles today, the four `just windows-*` commands that form the developer loop with no Windows machine, the two owner decisions W3 should not start without (regex, `proc_run` — both flagged in their own entries below), and the two items W2 handed to W3. Driver: **uncharted-suns is intended to ship on Steam**, which needs a Windows `.exe`; that game links this repo's `runtime/*.c` directly (its `Justfile:106`), so the game's Windows build is gated entirely on this runtime. Scope is the runtime and nothing else — **codegen is already portable**: `ir_lowering.sprout` emits `target triple = "unknown-unknown-unknown"` with no datalayout, and committed golden IR (including `musttail` and `llvm.stacksave`/`stackrestore`) cross-compiles to clean Win64 COFF objects at exit 0 for both `x86_64-pc-windows-gnu` and `aarch64-pc-windows-msvc` — reproducible with `clang --target=x86_64-pc-windows-gnu -c tests/golden/ir/examples__astar.sprout.ll -o /tmp/x.obj`. Toolchain decision: **develop with mingw-w64, ship with MSVC**, enforced by writing the Windows backend against pure Win32 + ISO C and never against mingw's POSIX shims (free if adopted at line one, expensive to retrofit). Rationale is risk, not Steamworks: Valve documents Windows support as MSVC-only, while `steam_api_flat.h`'s plain-C exports are reachable from any ABI. Minimum OS: **Windows 10 version 2004** (see W2). **Standing constraint (Kuba, 2026-08-16): the port changes no macOS or Linux behaviour or logic** — Windows arms go alongside the POSIX code, never through a refactor of it, and a Windows change needing shared-POSIX rework waits for the milestone owning that surface (see `docs/windows-port-v0.md` §2, which also records the one pre-constraint delta from W0b). Milestone B — running the *compiler* on Windows (bash `justfile`/`scripts`, bootstrap seed, `mise`) — is explicitly out of scope; see its own entry below.
  - [x] `P1` **W0a — measure the target (2026-08-15).** `scripts/windows_probe.sh` + `just windows-probe`: probes each header and each function independently against a mingw-w64 sysroot (a missing `#include` is *fatal*, so a plain compile reports one blocker per run and hides the rest; a present header does not imply a present function). Result at mingw-w64 14.0.0: **56 available, 22 missing**, and **every Win32 replacement the design names is available** — nothing is blocked on a missing API. Three corrections to the pre-measurement inventory, all folded into `docs/windows-port-v0.md` §6: (1) **`pthread.h` is present** (winpthreads), so the 2 async-DNS `pthread_create` sites need no work under mingw — `CreateThread` was listed as required and is not, though it returns under MSVC; (2) **`dbghelp.h` is absent** from the sysroot, so W4's backtrace is `CaptureStackBackTrace` and DbgHelp symbolization is off the table, making symbol names a separate question rather than a W4 blocker; (3) **`_ftelli64` is available under both** mingw and MSVC while `ftello` is mingw-only, so `_ftelli64` is the choice that satisfies the write-to-the-MSVC-strict-surface rule. Also established the per-TU ordering: `sprout_poll.c:94` stops on `sys/epoll.h` (it reaches the *epoll* arm, because `#ifdef __APPLE__`/`#else` treats "not macOS" as "Linux" — Windows needs a real three-way split, not an arm after the `#else`), `sprout_scheduler.c:30` on `ucontext.h` (so W0b is that TU's entire blocker), and `sprout_runtime.c:7` on `regex.h` (the *first* line of the biggest TU, which makes regex an ordering constraint as well as a feature question).
  - [x] `P1` **W0b — the `sprout_context.h` seam (2026-08-15).** `runtime/sprout_context.h`, a 4-op seam (`ctx_adopt_current`/`ctx_create`/`ctx_switch`/`ctx_destroy`) over the `ucontext` calls and all three stack frees. This is the one place an `#ifdef` in place will not do, because `ucontext_t` is embedded by value in `Task` and a fiber is an opaque `LPVOID`; everywhere else follows the existing precedent (9 `#ifdef __APPLE__` blocks in `sprout_runtime.c`, two backends in one `sprout_poll.c`). Contains **no Windows code** — a `_WIN32` include trips an `#error` naming W1, so `just windows-probe` now reports the scheduler's blocker as that `#error`. The decisive design constraint is stack ownership: `makecontext` runs on a stack you hand it, `CreateFiber` allocates its own, so **`SproutCtx` owns its stack and no op takes a caller-supplied one** (`Task`'s `ucontext_t ctx` + `void* stack` collapse into one field). Consequences, all in `docs/windows-port-v0.md` §4.6: `ctx_switch` keeps a `from` that `SwitchToFiber` ignores, documented as a precondition since all three call sites pass the running context; `ctx_create` returns a status so each caller keeps its own failure wording, byte-identical to before **including `"getcontext failed"`, which a non-POSIX arm must reword**; and one deliberate delta — the pump's stack moves from a 256 KiB BSS array to a constructor-time `malloc`, unavoidable under `CreateFiber`. Incidental: the `-Wdeprecated-declarations` suppression narrows from the whole 1300-line scheduler to the four calls needing it (TU verified clean under `-Wall -Wextra -Wdeprecated-declarations`). Gates: `just test`, `task-io-smoke` (kqueue) + `linux-smoke` (epoll+timerfd) — 43 scenarios each incl. GC-stress and the ASan-verified select/chan force-drop negative controls, i.e. exactly the free-ordering this touches — `compile-examples-stage1`, the 5-example run canary, `gc-safety-check --strict`, `check-approved-builtins`.
  - [x] `P1` **W1 — green threads: `ucontext` → Win32 Fibers (2026-08-16).** W0b's shape held exactly: W1 filled in four function bodies and changed **no scheduler logic**; the single call-site edit was `sprout_ctx_adopt_current` gaining a return code, since `ConvertThreadToFiber` can fail where the POSIX no-op cannot. **The two-toolchain rule paid off on the gate's first CI run.** W1 briefly also claimed `sprout_scheduler.c` as a whole, on a local mingw build: `sprout_scheduler.h` only *declares* the poller interface, so nothing on the scheduler's path reaches a POSIX poller header, and mingw compiled it clean. MSVC refuted it — line 30 is `#include <unistd.h>` for `close()`, which mingw supplies and MSVC does not. True of the developer surface, false of the ship surface; exactly the failure §4.1 predicts, caught at W1 instead of W5. That TU is now outstanding against W3, its blocker being the `close()` → `closesocket()` substitution (Winsock work, not scheduler work). **W1's original exit criterion was unachievable** ("task spawn / yield / join / `scope_cancel` smoke passes" needs *running* on Windows, impossible before W5 links an executable) — the same class of error as W0's, now corrected to compilation, gated by `scripts/windows_tu_check.sh` (`just windows-tu-check`, and a step in the `windows` CI job). **The find that mattered: `FIBER_FLAG_FLOAT_SWITCH`.** `CreateFiberEx` with `dwFlags = 0` does not switch FP state, and Sprout compiles `Float` to real `double` instructions (`ir_lowering.sprout:115-118`), so a yield mid-computation would silently corrupt arithmetic — no crash, no diagnostic. Passed unconditionally: redundant on x86-64 (where `winnt.h` defines `CONTEXT_FULL` to already include `CONTEXT_FLOATING_POINT`) and load-bearing on 32-bit x86 (where it does not), which is exactly why Microsoft words the hazard as x86-only. Other decisions in `docs/windows-port-v0.md` §4.7: `commit = 0` / `reserve = stack_bytes` stack sizing, the `adopted` flag that keeps `DeleteFiber` off task-0, and a trampoline rather than a function-pointer cast to `LPFIBER_START_ROUTINE`.
  - [x] `P1` **W2 — Windows poller backend (`WSAPoll`) (2026-08-16).** Third arm in `sprout_poll.c`, placed **first** in the chain (`#if defined(_WIN32)` / `#elif defined(__APPLE__)` / `#else`) because the old `#else` reads "not macOS" as "Linux", so an appended Windows arm is dead code — W0a predicted this and the RED gate confirmed it (`sprout_poll.c:94: fatal error: 'sys/epoll.h' file not found`). Both POSIX arms textually untouched, per the standing no-POSIX-change constraint now recorded in `docs/windows-port-v0.md` §2. **The find that shaped the backend: `WSAPoll` cannot wait on an empty socket set.** Microsoft: *"The array must contain at least one structure with a valid socket"*, and `WSAEINVAL` *"if none of the sockets ... were valid"*. The pump blocks in `sprout_poll_wait` whenever anything is parked (`sprout_scheduler.c:401`), including a set that is entirely timers — a lone `task_sleep` makes one, and for uncharted-suns (poller load = timers, not sockets) that is the *common* path, not an edge case. kqueue/epoll never meet it because each exposes a timer as a pollable object in the same wait set. Resolution: a timers-only wait uses `Sleep(nearest deadline)`, safe because the runtime's only cross-thread wakeup is the detached `getaddrinfo` thread and a pending DNS park is a socket park — the line to revisit if a second wakeup source ever appears. Other decisions in §4.8: **timeout clamped to `[0, INT_MAX]`** (an already-due deadline computes negative, and `WSAPoll` reads negative as *wait indefinitely* — an unclamped subtraction would hang the code path that exists to prevent hangs); due timers harvested on **both** return paths, since a deadline and a ready socket can land in one wait and `PARK_FD_TIMER` is registered on each; a **compact** registration array rather than negative-fd holes, so array length and live count cannot diverge; **monotonic never-reused timer ids**, which is what makes `remove_timer` safe against a stale token (§5.1) with an O(concurrently-parked) scan instead of an id→slot index every heap sift would maintain. `sprout_poll_init` is empty — `WSAStartup` is W3's. **Deferred to W3 on purpose:** the interface's `int fd` (a `SOCKET` is `UINT_PTR`; W3 widens it with the handle table) and the DNS-pipe swap (below). Gates: `sprout_poll.c` promoted to `windows_tu_check.sh`'s EXPECTED (mingw locally, MSVC in CI), **plus a new behavioural one** — `tests/windows/poll_selftest.c` + `just windows-poll-selftest`, which builds and RUNS the backend on the CI runner against real loopback sockets. W2 breaks the compile-only pattern deliberately: the poller's dependency set is `Ws2_32` alone (no scheduler, no GC, no runtime), so it need not wait for W5, and unlike W3's substitutions its logic branches — the empty-socket set, the clamp, a swap-remove during a live scan — where a bug deferred to W5 would surface tangled with four other milestones'. Seven cases incl. timers-only ordering, one-shot non-re-reporting, deadline-beats-idle-socket, and fd+timer in one batch; it also exercises the loopback-pair construction W3 needs for the DNS fix. POSIX gates run and green: `just test`, `task-io-smoke` (43 scenarios), `linux-smoke`, `compile-examples-stage1`, the 5-example canary, `gate-audit`.
  - [ ] `P1` **Async DNS parks on a `pipe()` — no Windows readiness poller can watch it (moved from W2 to W3, 2026-08-16).** `sprout_runtime.c:7102` uses a `pipe()` read end as the completion signal from the detached `getaddrinfo` thread; neither `WSAPoll` nor AFD (socket-only, being the driver beneath Winsock) can poll it. Fix: a self-connected loopback pair — the channel carries exactly one completion byte, so it is equivalent. Winsock has no `socketpair()`; the emulation is `bind`/`listen(1)`/`getsockname`/`connect`/`accept`, confirmed by libuv hand-rolling exactly that (`src/win/tcp.c:1627`). Sprout can use a blocking `accept` where libuv needs `AcceptEx` (libuv requires overlapped handles for IOCP; this pair is built once on one thread). **Why it moved:** the call sits in `async_resolve` alongside `fcntl`/`read`/`close`/`pthread_create`, in a TU that does not compile for Windows at all until W3 clears `regex.h` at line 7 — so a W2 arm would have been code no gate could compile, in the one function where a slip breaks the POSIX DNS path. W2 left a comment at the site instead, so someone editing DNS learns the constraint without reading the design doc.
  - [ ] `P3` **AFD/wepoll poller backend — gated on evidence, not a design question.** Deliberately NOT adopted at W2 (`docs/windows-port-v0.md` §4.3.1). The reason it was demoted from "fallback" to "contingency": **it buys no capability.** wepoll's README says *"Only works with sockets"* — structural, since AFD *is* the Ancillary Function Driver beneath Winsock — so it reaches no further than `WSAPoll` and does not solve the DNS-pipe problem either. Its only advantage is a registered set instead of a re-passed array, i.e. scale, which is bounded here by `g_conn_fd[2048]` (`sprout_runtime.c:153`) → ~16 KB copied per wait worst case, over *currently-parked* fds only since registration is one-shot. Cheap to adopt later: the poller is a 6-function interface (`sprout_scheduler.h:71-100`) and a fourth backend is the same contained one-file change a third is. **Re-open on any of:** (1) a *measured* `WSAPoll` bottleneck — nobody has benchmarked it under Sprout, and a measurement beats the reasoning; (2) `g_conn_fd`'s 2048 cap being raised toward five figures — **open question whether that is a deliberate ceiling or an arbitrary table size**; (3) a need to poll a non-socket other than the DNS pipe, which forces IOCP rather than AFD. Cost if adopted: undocumented NT interfaces (`\Device\Afd`, `IOCTL_AFD_POLL`) plus vendored third-party code, which needs explicit approval.
  - [ ] `P3` **IOCP poller backend — the one option that is NOT cheap to revisit.** Recorded so the asymmetry is not forgotten: swapping `WSAPoll` for AFD is a one-file change behind the existing interface, but IOCP is a *completion* model and reshapes the park protocol itself. That, not performance, is why it stays out of W2. It is nonetheless the only option that reaches non-socket handles (named pipes support overlapped I/O), so a future requirement to park on something that is neither a socket nor the DNS pipe lands here directly. What Go (`netpoll_windows.go`) and libuv use.
  - [ ] `P1` **W3 — Winsock, files, arena, threads, console.** Winsock2 (`SOCKET` is not an `int` fd; `WSAStartup`, `closesocket`, `WSAGetLastError`) across every `tcp_*` builtin and the handle table. **Includes `sprout_scheduler.c:30`** — its `#include <unistd.h>` for `close()` is the TU's only remaining MSVC blocker (measured at W1; mingw compiles it, MSVC does not), and the call it guards is `force_drop_task` closing an unowned-fd park's socket, so it becomes `closesocket` with the rest of the socket surface. Promote that TU to `windows_tu_check.sh`'s EXPECTED list when it lands. **Two items W2 handed over:** the DNS-pipe → loopback-pair swap (own entry above), and **widening the poller interface's `int fd`** — `sprout_poll_add`/`_remove` take an `int` that predates Windows, and the WSAPoll arm casts to `SOCKET` (`UINT_PTR`); the cast goes away when the handle table converts. Also: `VirtualAlloc` `MEM_RESERVE`/`MEM_COMMIT` for the `mmap(PROT_NONE)` GC arena (a direct equivalent, the easiest item); **no work for the 2 async-DNS `pthread_create` sites under mingw** (W0a measured `pthread.h` + winpthreads as present; `stdatomic.h` is C11 and portable) — `CreateThread` returns only if/when the MSVC target is built; `SetConsoleMode` + `ENABLE_VIRTUAL_TERMINAL_PROCESSING` for the 14 `termios`-family occurrences (the escapes `stdlib.terminal` emits work on Windows 10+); `GetModuleFileNameA` for `readlink`/`_NSGetExecutablePath`; `_ftelli64` (not `ftello` — only `_ftelli64` exists under both toolchains) at `sprout_runtime.c:7241`; stub `getrlimit`. Surfaces with no implementation return the established `"…unsupported on this platform"` shape (precedent `sprout_runtime.c:7676`) — never a silent success.
  - [ ] `P2` **W4 — crash diagnostics.** 10 `sigaction`/`sigaltstack` occurrences → `AddVectoredExceptionHandler` (W0a confirmed `signal.h` exists but `sigaction`/`sigaltstack`/`sigemptyset` do not, so there is no POSIX path to fall back on); 13 `backtrace` occurrences → `CaptureStackBackTrace`. **Not DbgHelp** — W0a found `dbghelp.h` absent from the mingw sysroot, so frames come back as raw addresses and symbolization is a separate question rather than part of W4. May degrade to a loud stub without blocking a ship, but not to silence: the alternate-signal-stack design exists precisely so a stack-overflow SIGSEGV prints a diagnostic instead of dying as a bare exit-139.
  - [x] `P1` **`windows` CI job + `windows-ir-gate` (2026-08-15).** Moved forward from W5 to W1: writing the fiber (W1) and poller (W2) work — the two milestones carrying the design risk — with no Windows verification, then meeting four milestones of problems at once, is the expensive order. Wine is the wrong local substitute (an independent Win32 reimplementation, and fibers/`WSAPoll` are exactly where a reimplementation diverges; also being retired — Homebrew's casks are disabled from 2026-09-01 and x86_64 Wine on Apple Silicon rides the Rosetta 2 wind-down), and a free GitHub-hosted runner is genuine Windows. Job `windows` in `.github/workflows/ci.yml`, **advisory** like `macos` (`test` stays the one required check), **green from day one and growing one step per milestone** — a job red until W3 trains everyone to ignore it. Today it runs `just windows-ir-gate` (`scripts/windows_ir_gate.sh`), which gates §1.1's previously-hand-checked claim that codegen is target-neutral: all 58 golden IR snapshots × `x86_64-pc-windows-msvc` and `aarch64-pc-windows-msvc`, verifying the **COFF machine type** in the object header rather than a zero exit status, and asserting the corpus still contains `musttail` so a golden refresh cannot silently drop the one ABI-sensitive construct. Needs only clang ≥ 16 — no runtime, sysroot or bootstrap — which is why it passes while all three C TUs still fail to compile for Windows.
  - [ ] `P1` **W5 — link, game, run smoke.** First `.exe`. The `windows` CI job (above) gains its final step: link and **run** a task/IO smoke against the `WSAPoll` backend, mirroring what the `macos` job does for kqueue. Builds with **clang targeting `x86_64-pc-windows-msvc`**, not mingw — the local loop is mingw, so MSVC must be the gated one or the pure-Win32 rule rots unobserved.
- [ ] `P2` **POSIX `<regex.h>` has no MSVC equivalent** (2026-08-15). One use, `regex_compile_ere` (`runtime/sprout_runtime.c:6039`), reached via `regcomp`/`REG_EXTENDED`. Not in the MSVC CRT. Filed separately from W3 because stubbing it removes a **language-visible** feature rather than an internal capability — the decision is vendor-a-small-ERE-implementation vs. narrow the feature, and it should be made on its own terms. **DECISION PENDING (owner) — W3 should not start without it**; `sprout_runtime.c` stops on this include at line 7, so it is also W3's first ordering constraint.
- [ ] `P2` **`proc_run` on Windows via `CreateProcess`** (2026-08-15). `fork`/`execvp`/`pipe` (3/3/7 occurrences) → `CreateProcess` + anonymous pipes, preserving the deadlock-safe separate stdout/stderr capture. Not needed by the shipped game, so not a Milestone A blocker; it *is* needed by Milestone B and by the game's offline `gen-living` bake. Note `analysis_service_driver.sprout:751` shells out via `["sh", "-c", …]` and there is no `sh` on Windows, so the driver needs its own fix beyond the builtin. **DECISION PENDING (owner):** implement, or ship a loud stub? Since the game does not need it, a stub is a legitimate answer rather than a compromise — but that is a product call, not a porting one.
- [ ] `P3` **Milestone B — run the *compiler* on Windows** (2026-08-15). Everything outside the runtime: the `justfile` and `scripts/*.sh` are bash, the bootstrap-seed flow assumes a POSIX shell, `mise` provisions the toolchain. Realistically MSYS2 or WSL rather than a native port. Strictly downstream of Milestone A — Sprout is self-hosted, so a Windows-native `sproutc` is itself a program whose runtime must already be ported. Also gates the path-handling work in the §V1 `stdlib.path` entry (Windows separators/drive letters), which is deferred on exactly this.

## Current Snapshot

- [x] Modules with explicit exports (`export`) are implemented.
- [x] HTTP response helpers exist in `stdlib/http.sprout`.
- [x] JSON types and helpers exist in `stdlib/json.sprout`.
- [x] `stdlib.net` defines typed TCP client result/error helpers.
- [x] `stdlib.net` wraps TCP connections/listeners in distinct handle types for user-facing APIs.
- [x] `stdlib.bytes` provides raw byte slicing plus big-endian integer helpers.
- [x] `stdlib.bytes` now includes UTF-8 encode/decode plus null-terminated string helpers.
- [x] `stdlib.bytes` now includes an efficient builder API for protocol packet assembly.
- [x] `stdlib.crypto` provides SHA-256, HMAC-SHA-256, base64, XOR, and entropy helpers for authenticated clients.
- [x] Swappable TCP server model exists (`reactor`, `blocking`) for server-side runtime.
- [x] `http_request` builtin and typed HTTP result ADTs are implemented in interpreter and native modes.
- [x] `stdlib.json` owns JSON types/helpers, and `json_parse` builtin plus basic JSON accessors are implemented there.
- [x] `stdlib.collections` now uses runtime-backed `Vector` for `Vec` indexing helpers.
- [x] `stdlib.collections` now uses runtime-backed `Map` for `Dict` helpers.
- [x] Runtime builtin failures now use a shared `runtime error: builtin ...` convention in interpreter and native paths.
- [x] CLI REPL exists with declarations, expression evaluation, and `:type`.
- [x] Prelude now includes `when_ok` / `when_error` effect taps for `Result`.
- [x] Prelude now includes `pipe_apply` (renamed from `pipe` to avoid POSIX `pipe()` collision) plus `result_pipe*` helpers for lighter `Result` pipelines.
- [x] CLI formatter/linter baseline exists (`fmt`, `fmt --check`, `lint`).
- [x] Native formatter/linter binary (`fmt_bin`) implemented in pure Sprout (2026-05-20): `stdlib/compiler/formatter.sprout` is the pure core (`format_source`, `lint_source`); `stdlib/compiler/fmt_driver.sprout` is the CLI driver (`main() -> Int`, subcommands `fmt`, `fmt --check`, `lint`); `just build-fmt` builds via Python stage-0 compiler; `just fmt-native`, `just fmt-check-native`, `just lint-native` are convenience wrappers; `tests/test_fmt_native.py` covers 11 cases including full 126/126 parity with Python formatter. Infrastructure added: `write_file` builtin (runtime C + prelude extern), `main() -> Int` exit-code support in both codegen paths, `string.join`/`string.repeat`/`string.replace_all` in `stdlib/string.sprout`. Algorithm is token-based and line-by-line (no AST needed), reusing `stdlib/compiler/lexer.sprout` directly. Notable: `!` is not a prefix boolean-negation operator in Sprout, so word-like guards are inlined via `is_word_like`; the keyword-branch space-suppression for `|` was a parity bug (fixed — now always returns `true` since `prev.value == "|"` in Python adds space via line 193 of `_needs_space`).
- [ ] terminal interaction primitives are not yet fully implemented.
- [x] Module exports now support opaque exported types via `export type Name` and constructor-exporting ADTs via `export type Name(..)`.
- [x] `env_get(name) -> Maybe String` is available in interpreter and native modes.
- [x] Integration-style IO tests now have a dedicated harness (`tests/integration_support.py`) and focused suite (`tests/test_integration_io.py`).
- [x] The builtin surface is now explicitly audited in the docs as `IO`-annotated, pure, or runtime-bound-but-non-`IO` in v0.
- [x] There is now an explicit design plan for promoting a minimal real effect system into v0: [docs/effect-system-v0-plan.md](./docs/effect-system-v0-plan.md).
- [x] Bootstrap self-hosting compiler modules exist in `stdlib/compiler/`: `source`, `token`, `lexer` (Python tokenizer parity), `ast`, `parser`, `types`, `unifier`, `infer` (HM constraint generation/solving).
- [x] Bootstrap parser parity harness: `driver.sprout` dumps AST as flat s-exprs; `tools/dump_ast.py` does the same via Python parser; `tests/test_parser_parity.py` confirms 7/7 corpus files match (one known `++`-desugaring divergence documented).
- [x] Bootstrap checker integration seam: `checker.sprout` + `type_driver.sprout` + `tools/dump_types.py` + `tests/test_checker_parity.py`; 6/6 corpus files match, no known divergences.
- [x] Phase 2 compiler driver: `compiler.sprout` (API) + `compile_driver.sprout` (executable) + `sprout.cli bootstrap-check` subcommand; at least one CLI check path now runs through Sprout-owned control flow end-to-end.
- [x] FnDecl body inference: `check_fn_body` in `infer.sprout` checks bodies against annotation schemes; unknown-ref leniency for builtins; real type mismatches propagate.
- [x] Builtin env seeded: ~25 common functions/constructors pre-populated in `checker.check_program` initial env so body inference resolves them without leniency.
- [x] ClassDecl/InstanceDecl: class methods registered globally as polymorphic schemes; instance method bodies checked against method annotations; checker corpus now 6/6.
- [x] Pattern match exhaustiveness checking: non-exhaustive matches on user-defined ADTs now produce a compile-time `[DIAG]` error citing the missing constructor names; wildcard/variable patterns are treated as catch-alls.

- [x] **Iface arc Phase 1** (shipped on `feat/iface-skeleton` / `feat/iface-ast-codec`): `encode_scheme`/`decode_scheme`; `IfaceFile` v2 wrapper with CtorInfo/ClassInfo/InstanceInfo tables; `extract_named_schemes`/`extract_ctor_infos`/`extract_class_infos`/`extract_instance_infos` from a typechecked program; round-trip tests in `test_scheme_roundtrip.spr`, `test_iface_file_roundtrip.spr`, `test_iface_extraction.spr`.
- [x] **Iface arc Phase 2** (shipped on `feat/iface-ast-codec`): AST codec — `encode_ast_program`/`decode_ast_program` and all constituent ADTs (`Expr`, `Pattern`, `Decl`, `TypeExpr`, `Param`, `MatchBranch`, `DoStep`, `RecordField`, `TemplateExprPart`, `TypeConstraint`, `TypeConstructor`, `RecordFieldDecl`, `ClassMethodSig`, `InstanceMethodImpl`, `SourcePos`); 58 round-trip assertions in `test_iface_ast_codec.spr`. Positions preserved faithfully for LSP go-to-definition.

## Next Steps

- Note: pure unifier (state-threading) was considered and decided against — keeping `Ref`-based mutable state in `InferState` for performance reasons.

## Design Roadmap

> Consolidated 2026-07-05 from the former `docs/backlog.md` (now retired — this is
> the single canonical backlog). The sections above are the engineering execution
> log; this section holds the forward-looking design/soundness priorities and the
> V1 roadmap, with their design-doc links preserved.

### Current Priorities

**Fundamentals-review fix campaign (2026-07-03).** An adversarial review of the runtime,
both codegen paths, the type system, and the prelude found confirmed soundness and
memory-safety holes: effect system unenforced, declared type variables not rigid, no
value restriction, class-method dict dispatch by wrong position, typed-path top-level
`let` globals never GC-rooted (silent use-after-free), UTF-8 out-of-bounds walkers +
unvalidated ingestion, `/` division UB, exhaustiveness gaps. Full findings, probe
programs, session-by-session fix plan, and the five user decisions needed (D1-D5) are in
[fundamentals-code-review-handoff-2026-07-03.md](./docs/fundamentals-code-review-handoff-2026-07-03.md).
**Decisions D1–D5 worked through 2026-07-04:** D1 (division = panic + stdlib `safe_div`;
`+`/`*` overflow = documented i64 wrap), D3 (retire the direct codegen path — **DONE 2026-07-12**, `codegen.sprout` deleted; typed IR is the sole backend), D4 (reject
invalid UTF-8, Bytes-primary via the `bytes_to_utf8` choke point), and D5 (total
`parse_int : Maybe Int` + delete dead `split_ints`; `mutvec_get : Maybe a`) all DECIDED;
**D2 (effects) DEFERRED** — W6 is blocked on an effect-system *design* pass, not rollout
shape. See §2 of the handoff doc for full rationale. W1 (global GC roots), W5
(exhaustiveness, done — see §1 "Language Core and Safety" above), and W2 R1/R3/R4 (UTF-8
runtime safety) already landed. W3 (rigidity + value restriction), W4
(dispatch-by-constraint-position), W7 (div-by-zero), and W8 (prelude totality) have since
landed too; W11 T8 (fresh-tyvar namespace) + T10 (inner-TApp return-type dict) landed
2026-07-08. Remaining unblocked correctness work: W9 remainder, W7's `INT_MIN / -1`
operator guard (coupled to the int-overflow policy), W2 R2, and T11 (iface-gated). W11 T7
(bare-name type identity) LANDED 2026-07-10. W6 (effects) stays DEFERRED pending an
effect-system design pass.

**Bare-name type identity — cross-module type-name collision. DONE 2026-07-10** (branch
`design/module-qualified-type-identity`, W11/T7). Type identity is now module-qualified.
The root-cause fix was "stop stripping," not a new resolver: `bundler.qualify_type_name`
already resolves references correctly (local-shadows-prelude), so ~37 `after_last_dot`
sites re-stripping the canonical name were the whole bug; the prelude's empty module header
makes its canonical identity the bare name, distinct from `main.Maybe`. The fix threaded
six representations (checker identity, the injected TDict head, three dispatch keyspaces,
the `__tc_` LLVM symbol, and `head_is_concrete`) — see
`docs/module-qualified-type-identity-design-2026-07-10.md` for the full anatomy and the
`head_is_concrete`-reads-the-lowercase-module-prefix-as-a-type-variable gotcha. Regression
guards: `tests/stdlib/compiler/test_type_name_collision_{shadow,instance_dispatch}.spr` +
`test_local_type_no_collision_control.spr`. Prerequisite for sound separate compilation in
the iface arc. Latent hardening left: `resolve`/`lowering` `is_type_var_name` lack the
dot-guard `infer.is_lowercase_name` has (a dotted name is never a type variable).

1. Execute Model C GC-rooting plan (typed Sprout-IR + linear types).
   Design doc: [gc-rooting-model-c-plan-2026-06-02.md](./docs/gc-rooting-model-c-plan-2026-06-02.md).
   Status (2026-07-29, verified): **M1 complete, M2 acceptance MET, M3 substantially
   complete.** The Sprout-IR path is now the *sole* codegen path — `--emit-ir` (the
   default) and `--use-ir-codegen` both dispatch through `ast_to_ir` + `ir_lowering`,
   and the old direct backend `stdlib/compiler/codegen.sprout` has been deleted (M3.2).
   M2 acceptance was re-verified end-to-end on a tree rebased onto `origin/master`:
   `just test` green through the IR path (only the 8 pre-existing `conformance/run`
   fixture-rot `xfail`s remain — see "conformance-run fixture rehabilitation"); the
   full `tests/stdlib{,/compiler}` corpus (230 suites) compiles+runs clean under an
   explicit `--use-ir-codegen` sweep; stage-1 self-compiles to a **byte-identical**
   fixed point (stage-2 re-emits identical IR); `verify-bootstrap-fixed-point` and the
   `test-stress` GC oracle (`SPROUT_GC_STRESS=1`, empty XFAIL) both green. The
   `clang_verifies_ir` promotion is DONE (all 8 IR-codegen suites adopt the shared
   `testsupport/ir_verify.sprout` helper). Five deferred items from the PR 2.3
   code-review pass are tracked in the "Language Core and Safety" section above.
   M2/M3 residuals — reachability assessment DONE (2026-07-29):
   - `TDict` expression: **NOT reachable — closed.** `lowering.lower_program` runs before
     `ast_to_ir` and eliminates every `TDict` (in-`TCall` witnesses → concrete instance
     fn-refs / hidden dict params via `expand_call_args`; a standalone `TDict` → `TUnit`,
     `lowering.sprout:1296`). So `ast_to_ir`'s `"TDict not yet supported"` arm is
     structurally-unreachable dead code; no implementation needed.
   - Refutable do-bind without `else` (e.g. `Just(x) <- e`): **was reachable — FIXED.**
     It type-checked then crashed at codegen with an internal `ast_to_ir` error (no
     fallback, since the direct backend is gone). Root cause: the parser's `<-` do-step
     accepted any pattern without `else`, unlike the sibling `let` do-step which already
     rejected refutable patterns. Fix: `parser.is_irrefutable_do_bind_pattern` gates the
     no-`else` `<-` arm (allows var/wildcard/unit/tuple-of-irrefutable — exactly what
     `ast_to_ir.do_bind_captures` lowers), else a parse diagnostic "refutable `<-` binding
     in a do block requires an `else`". Prior-art survey (Rust/Swift require an explicit
     refutable construct; Scala 3 tightened toward this; Haskell/OCaml allow-with-fallback)
     backed the reject-at-parse choice, consistent with Sprout's existing `let..else`.
     Regression: `tests/conformance/parse_error/refutable_do_bind_no_else.spr`.
   With both residuals resolved, the M2/M3 codegen-parity axis is closed; next is M4
   (user-facing linear types).
   List-pattern sugar — pending sweeps (2026-06-08):
   - Expression-side sweep: rewrite multi-element `Cons(a, Cons(b, …))`
     *constructions* in `ast_to_ir.sprout`, `compiler.sprout`, and similar
     files to `[a, b]` / `[a, b, c]` literals.  Stage in batches with a
     bootstrap-seed refresh per batch to keep diffs reviewable.  See
     `docs/style-guide-v0.md` §8 for the policy on what to rewrite vs leave
     (sugar wins for 2+ heads; single-head `Cons(x, …)` constructions and
     `| Cons x rest ->` arms stay long-form).
   PR 2.5 (first /code-review pass) follow-ups:
   - [x] Refactor the ctors-dict tuple `(tag, arity, max_arity, field_kinds_string)` in
     `ast_to_ir.sprout` to a named record — DONE (named `sprout_ir.CtorInfo`, not the
     provisional `CtorMeta`). Done ahead of a 5th-field trigger at user request. Actual
     scope was larger than estimated: 15 destructure sites (2 inline) + 2 construction
     sites + 107 dict-value signatures across `ast_to_ir.sprout` + `ir_pipeline.sprout`.
     The "risk of reintroducing the same bug class" was closed by (a) a clean full-driver
     `--phase check` proving every site converted, and (b) byte-identical emitted IR across
     all smoke shapes + 5 canary examples (CtorInfo is compile-time-only, never emitted).
     Field-order regression pinned by `tests/stdlib/test_ir_ctor_info.spr`. Note: the
     trivial-accessor codegen bug (`project_trivial_accessor_codegen_bug`, item below) did
     NOT recur — it was `codegen.sprout`-specific and that backend is retired.
   - Split the field-kinds encoding's `'s'` byte into distinct `'s'` (String, heap)
     and `'c'` (Char, scalar) codes in `stdlib/compiler/field_kinds.sprout` so Char
     fields stop being conservatively over-rooted (small per-ctor-field perf win).
     Now a single-file edit since the encoder is consolidated (PR 2.5 /code-review
     fix #10). Trigger when Char field rooting becomes measurable, or on the next
     scheduled cleanup pass.
   After M2: flip default to `--use-ir-codegen` (M3), then linear types as a user-facing
   feature (M4), then apply linearity to Sprout-IR (M5) so GC rooting correctness becomes
   a theorem rather than a discipline.
   Native REPL groundwork is complete: the combined `build/sproutd` binary (sproutd M3,
   2026-05-26) launches as REPL by default and as the analysis service with
   `--analysis-service <stdlib_root>`. `sproutd_self_init()` auto-resolves stdlib root
   from the executable path; `SPROUT_ANALYSIS_SERVICE_CMD` still works as an explicit
   override. `just repl` wires this launcher. The minimal LSP layer (sproutd M4,
   `stdlib/compiler/lsp_driver.sprout`) handles `initialize`, `textDocument/didOpen/
   didChange/didClose`, `textDocument/hover`, and stub completion.
2. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
   Native-performance follow-up:
   - make tight Sprout string-processing loops competitive with host builtins so moderate stdin/text workloads do not require dedicated host helpers just to be practical
   - investigate the remaining native overhead in recursive stdlib string loops such as `string_lines` over stdin-loaded text, with focus on tail-recursive loop lowering, call/closure overhead, primitive boxing, and efficient string/vector iteration
   - add stable native performance benchmarks for `string_lines`, `trim`, and AoC-style stdin parsing so regressions and wins are measurable
   - target: native `string_lines` over stdin-loaded text on the current `day5input`-style workload should complete in low single-digit seconds rather than tens of seconds
3. Add stronger server-side runtime models (multi-reactor as next target).
   Recent groundwork landed: native TCP handle-slot reuse and an experimental `stdlib.http_server` helper layer for structured request parsing/rendering.
   Remaining follow-up: incremental bytes-oriented HTTP reads, keep-alive/chunked support, and stronger concurrent runtime models.
4. Keep expanding stdlib text/data helpers beyond the current baseline (`trim*`, `contains`, `ends_with`, `string_lines`, `string_digits`, vector utility combinators).
   Remaining follow-up: define the Unicode text model explicitly enough to support a future `Char` type and consistent string indexing/length/slice semantics.
5. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
   (The immediate `fmt_bin fmt` single-line-collapse bug is tracked as its own `[ ]` item in the "Tooling and Developer UX" section above — this entry is the broader roadmap for formatter/linter depth.)
   Three AST-based rules landed (`staircase-of-doom` 2026-07-20, `redundant-vec-from-list` 2026-07-21, `list-shape-pattern` 2026-07-21) — see the `[~]` item above for detail and the roadmap of further `docs/idiomatic-sprout.md` coverage.
6. Keep improving local test throughput.
   Completed: native Sprout test files for all six compiler stages live in `tests/stdlib/compiler/` — `test_lexer.spr`, `test_bundler.spr`, `test_parser.spr`, `test_checker.spr`, `test_lowering.spr`, `test_codegen.spr`; run via `just test` (`test-stdlib-stage1`).
   Completed: fixed bundler UTF-8 bug in `strip_headers_b` — byte offset was used as codepoint index in `str_slice`, causing parse failures on files (e.g. `stdlib/compiler/types.sprout`) with multi-byte characters in comment headers.
   Remaining follow-up:
   - add compile caching for the stdlib test runner so repeated runs of unchanged test files skip the IR-emit + clang step (see also "Self-Hosting Follow-Ups → Incremental build caching" below — a shared invalidation strategy could cover both).
7. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.
8. Continue native memory-management v1.
   Design doc: [native-memory-management-v1-draft.md](./docs/native-memory-management-v1-draft.md).
   Completed groundwork: allocation visibility, centralized managed allocation for Sprout values, heap metadata hooks, and an initial non-moving stop-the-world mark-sweep collector with default threshold-triggered in-process collection in the native profile. (GC memory phase 1 — exact-size `SproutObj` — is done; see "Language Core and Safety" above.)
   Remaining v1 scope: close the remaining path-specific live-value gaps outside the current shadow-root coverage, keep validating and tuning the current default threshold (`4096` managed nodes) with the new per-cycle live-heap/timing diagnostics, and keep expanding reclamation-focused validation.
   V2 direction: pause/throughput improvements only after v1 is measured, likely via incremental or generational follow-up work if justified.
9. **Incremental partial application miscompiles (SIGSEGV).** Applying a partial closure
   one argument at a time when two or more arguments remain crashes at runtime.
   `add3(1)(2)(3)` (with `fn add3(x, y, z)`) typechecks — the checker is currying-correct —
   but codegen builds an arity-2 partial closure (`__sprout_partial_N(env, a0, a1)`) and the
   next call site applies a single argument, under-saturating it; the malformed call returns
   an `Int` that the following application reinterprets as a closure pointer (`inttoptr` +
   `load`) → SIGSEGV. Saturating the partial in one call (`feed_two(add3(1), 2, 3)`) works, so
   only incremental application is affected. **RULED 2026-07-26: Package C-b** (n-ary +
   `_`-placeholder partials) — see
   [currying-and-pipe-decision-v1.md](./docs/currying-and-pipe-decision-v1.md). Under C-b the
   segfault is fixed by rejecting under-saturating application at the checker as a clean arity
   error (n-ary), not by building curried partials.
   **Part 1 LANDED:** `_`-placeholder partial application — `add(_, 3)` desugars to a lambda at
   parse time (`desugar_placeholder_call` in `parser.sprout`); regression
   `tests/stdlib/test_placeholder_partial.spr`. Gives first-class, any-position partials to pass
   around, with no curried closure ABI. Normative in `spec-v0.md` §5.3.
   **Part 2 LANDED (kills the segfault): n-ary arity checking (Approach B).** The checker rejects
   under-application of a known function — `add3(1)`, `add3(1)(2)(3)` → `'add3' expects 3
   arguments, got 1` — before the malformed partial is ever built. A top-level function's declared
   param count is stored as an `@arity:{name}` env marker (`pre_scan_fn_decls`); `infer_call_var`
   rejects `args < arity` via `under_application_error` (`infer.sprout`). Scope: direct calls to
   known-arity functions/externs. Fixtures `tests/conformance/type_error/nary_{under_application,
   incremental_chain}`; `test_ir_partial_application` + `test_function_returning_function` migrated
   to `_`-placeholder form. `spec-v0.md` §5.3 under-application clause retired (now n-ary).
   **Part 3 / Approach A (OPEN, completeness) — arity-aware types.** B enforces only at direct
   call sites; under-application *through a function-typed value* (a `f: A->B->C` parameter applied
   to fewer args) is not caught, because `TFunc` is curried and carries no arity. A gives the type
   system a real n-ary arrow (or an arity tag on `TFunc`) so arity flows through unification/
   generalization — catching all under-application and improving diagnostics. Large type-system
   change (unification, generalization, every function type); B's semantics/tests/migration carry
   forward and A deletes B's checker pass. Do after B beds in; likely deferrable indefinitely since
   most residual cases already surface as function-vs-expected type mismatches (e.g. the two-hole
   pipe edge).
   **Deferred to v2:** operator sections (`_ * 2`, `10 - _`) — parser represents operators as a
   distinct `BinaryExpr` node, so sections need a second small handler.
   Applicative stays useful under C-b via `map2`/`map3`/`traverse` (saturated application);
   `ap`/`<*>` is not the idiom. Counterpart defect (checker rejecting function-typed returns
   because `ctor_result_type` over-stripped arrows) is fixed in `infer.sprout` via
   `fn_return_type`; regression: `tests/stdlib/test_function_returning_function.spr`.

### V1 Roadmap Candidates

1. Add list comprehensions for `List` values in v1.
   Initial scope: `[expr for x in xs]` and `[expr for x in xs if pred]`.
   First milestone constraints: single generator, optional guard, list-only, no pattern generators, and no nested or multi-generator comprehensions.
2. Continue the experimental integer-ranges slice toward a final v1 contract.
   Current implemented scope: a dedicated `IntRange` type, `a..b` inclusive syntax, ascending and descending unit-step semantics, and helper surface including `range`, `range_start`, `range_end`, `range_contains`, `range_count`, `range_to_list`, `range_to_vec`, and `range_fold`.
   Remaining follow-up: finalize the normative v1 contract, keep diagnostics sharp, and decide whether later range extensions such as patterns or half-open forms should exist at all.
3. Expand native ADT lowering in v1.
   Design doc: [native-adt-lowering-v1.md](./docs/native-adt-lowering-v1.md).
   Initial completed slice: native `Nothing` singleton plus immediate-match optimization for direct constructor-producing scrutinees.
   Planned follow-up: broader constructor forwarding, whole-scrutinee binding support, and specialized representations for tiny ADTs.
4. Harden the language/runtime prerequisites for external protocol client libraries built on top of `stdlib.scram`, `stdlib.net`, and `stdlib.bytes`.
   Initial scope: keep the byte-building/parsing, TCP, crypto, and generic SCRAM surfaces stable enough for a separate repository to implement protocol-specific auth and wire flows.
   First milestone constraints: no protocol-specific client implementation in this repository, keep host-side builtins minimal, and prefer generic helpers that external libraries can compose.
5. Add records in v1.
   Initial scope: immutable record values with explicit field names, field access, and straightforward construction/update rules that preserve Sprout's strict evaluation model.
   Surface design DECIDED — see `docs/records-v0.md` (dedicated draft/spec, non-normative until implemented). Fixed surface: declaration `type P = (x: Int, y: Int)`, construction `P(x = v, y = v)` (tag-prefixed, `=`), access `p.x` (dot; resolver rule, variable-chain "Scope A" only), update `p with (x = v)` (reuses the `with` keyword; no `..` spread — collides with ranges/export-all). `:` = has-type (declarations), `=` = has-value (construction/update); no braces (records join the paren/product family, kept visually distinct from `Dict`'s `{k: v}`).
   LANDED (PR1, branch `feat/records-v0`): the v0 declaration/construction/access surface now works end-to-end on the active IR codegen path. Parser reads `type P = (x: Int, y: Int)` (paren-labelled fields; `is_record_scan` keys on `=` then `( ident :`) and `P(x = v, ...)` construction (disambiguated from a call by the `ident =` lookahead); the brace-literal and `get p x` prototype forms are removed (`get` reverts to a plain identifier). Dot access is an inference-time name-resolution rule (`infer_var_or_field`, head-first so a local wins over a same-named module): a dotted `VarExpr` whose head is an in-scope value rewrites to a `GetFieldExpr` chain, else falls through to the plain/module-qualified lookup. Codegen registers a record as a single-constructor product in the ctor table (`sprout_ir.CtorInfo` gained a `field_names` slot; no `CtorReg`/runtime registration — GC tracing is header-driven), lowering `TRecord` → reorder source→declaration order → `IRMakeCtor`, and `TGetField` → offset-resolved `IRGetField`. `tests/conformance/run/record_types.spr` migrated to the v0 surface; new gated `tests/stdlib/test_records.spr`. Normative records section added to `docs/spec-v0.md` §5.6.3. Self-compilation fixed point holds (dot-rule is a no-op on the compiler's own dotted names).
   GC rooting is exercised and gated: `tests/stdlib/test_stress_records_heap.spr` holds a record and its extracted heap fields live across churn and passes under `SPROUT_GC_STRESS=1` — it is in `just test-stress`'s `STRESS_FILES`. (Records go through the *optimized* rooting/alloc-summary path, not a conservative fallback: `op_triggers_gc(IRMakeCtor)=true` classifies record construction as allocating, and `IRGetField`'s `IRTHeap` kind roots a loaded heap field — records reuse the ADT ops, so the op-based pass covers them.)
   LANDED (PR2): **functional update `base with (field = value, ...)`.** New AST node `RecordUpdateExpr` (typed `TRecordUpdate`) threaded through the whole pipeline (parser postfix — `with` recognised only before `( ident =`, so it never captures a `match ... with`; qualify/desugar/lint/iface-codec/free-vars/DCE/resolve/lowering/verify walkers; inference `infer_record_update` checks each named field belongs to the base's record type and unifies its value, result type = base type). Codegen (`build_update_args`) evaluates the base ONCE, then for each declaration-order field either translates the new value or copies it from the base via `IRGetField`, and emits one `IRMakeCtor`; supports chained `p with (...) with (...)` and any base expression. Immutable (allocates a fresh record). Tests: `tests/stdlib/test_record_update.spr`; update rooting added to the gated `test_stress_records_heap.spr` (base held live across the update allocation + copied heap fields survive `SPROUT_GC_STRESS=1`); `RecordUpdateExpr` iface round-trip in `test_iface_ast_codec.spr`. Spec §5.6.3 documents the update form.
   Code-review fixes LANDED (PR2 follow-up, `/code-review` findings 2–5): duplicate-field diagnostic for construction AND update (`field \`x\` supplied twice for record P`; `tests/conformance/type_error/record_dup_field_{construct,update}.spr`); `with`-update field-type check now applies the base record's **type arguments** to the declared field type (`concrete_field_type` substitutes the record's type params into the field-scheme body instead of a no-op instantiate — so `b: Boxed Int; b with (value = "str")` is now correctly rejected; `record_update_parametric_mismatch.spr`); clearer "not a record" update diagnostic (`record_update_non_record.spr`); and removed the duplicate `record_name_of_type` (→ reuse `type_head_name`) / simplified `record_head_name`.
   - [x] `P2` **Imported (cross-module) records: the "broken end-to-end" bug is GONE** (was code-review finding 1, confirmed 2026-07-23; re-verified and closed 2026-08-10). The original repro (`import stdlib.other (Pt, mk)` then `p with (x = 99)` -> ``record Pt has no field `x` ``) no longer reproduces: the `@rec:<name>:<field>` markers now resolve to the same identity at registration and at every use site. Verified across **22 shapes** in `tests/stdlib/test_imported_records.spr` -- construction at the use site, field access, `with` update (local, chained, self-referencing, and applied in a *third* module), parametric instantiation at two type arguments, nested records, function-typed fields called inline, per-record field scoping, records in a `List`, a record as an ADT constructor's field, derived `Eq`/`Ord`, and the linear-record single-read consumption rule. Six negatives still reject correctly (unknown field in `with`, unknown field access, missing field, wrong field-value type, linear reuse, linear drop), so no soundness was traded for it. **Why it sat open long after being fixed:** the entry's own reasoning -- "no committed runnable xfail test ... without a stdlib-resident fixture module polluting every build" -- was obsolete. Every test runner already passes `--package-root {{justfile_directory()}}`, so a fixture under `testsupport/` is importable from any test and touches neither `stdlib/` nor the bootstrap seed (`testsupport/linear_res.sprout` was already the precedent). Generalizable lesson: **a known-bug entry with no test has no mechanism to notice it has been fixed.**
   Remaining follow-up: **PR3** -- record-vs-tuple / record-vs-call / shadowing parser tests, plus the §8 error-message fixtures. **Correction (2026-08-10): this line's claim that "the diagnostics exist in inference" is false for two of the three, and its imported-records blocker is gone.** Measured: `with`-unknown-field and wrong-field-value-type ARE caught in `check` with a source position; unknown field *access* and missing-field *construction* are not caught by inference at all -- see the entry below.
   - [ ] `P2` **Unknown field access and missing-field construction bypass the typechecker** (found 2026-08-10 while verifying imported records). `fn bad(c: Cfg) -> Int = c.nope` and a `Cfg(header_ms = 1)` literal with fields omitted are both rejected only at `ast_to_ir`: `ERROR: ast_to_ir: record 'Cfg' has no field 'nope'` / `record 'Cfg' literal is missing a declared field` -- **with no source position**, and preceded by a misleading `warning: alloc-summary pre-pass failed (...); using conservative rooting + no mutual-TCO`, because the pre-pass treats a user type error as an infrastructure failure. **Reproduces same-module too** (control run), so this is a general records-diagnostics gap, not a cross-module one. Contrast the two paths that get it right: `with`-unknown-field gives ``2:27: ERROR: check: `with` update: no field `nope` on `Cfg` `` and a wrong field-value type gives a positioned `check` error -- so the fix is to give access and construction the same inference-level treatment those two already have. **The rejection is correct in every case; only the phase and the diagnostic are wrong**, which is why this is P2 and not P1. Repros are the four negative probes recorded in the closed entry above; they belong in `tests/conformance/type_error/`.
   - [ ] `P2` **DECISION NEEDED -- derived `ToString`: qualified or bare name?** (found 2026-08-10). `to_string` on a type with `deriving (ToString)` prints the **declaring module's qualified name** when the type is imported and the **bare name** when it is declared in the entry file: `testsupport.rec_fixture.Point(x = 1, y = 2)` vs `Point(x = 1, y = 2)`. Applies to records *and* ADT constructors, and compounds per nesting level. An aliased import (`import m as base`) still prints the full canonical path, not `base.Point`.
     - **Not a spec violation.** Spec §12's stated goal is that the output be valid source, and `app.models.Point(x = 1, y = 2)` *is* writable and compiles (verified) -- arguably more robustly valid than the bare form, which only parses where the type was imported. The disambiguation argument for qualification is real.
     - **The defect is the inconsistency, not the convention.** It is an artifact of `bundler.sprout:1126` running `deriving.expand_deriving_decls(qualified)` on already-qualified decls, so `deriving.sprout:159` `build_to_string_branch_body` reuses the qualified name as the *display literal* (correct for the match pattern, incidental for the string). Nobody chose it, and the consequence is that **extracting a type from the entry file into a module silently changes program output.**
     - **Prior art, verified against primary sources.** Haskell 2010 derived `Show`: "contains only the constructor names defined in the data type" (unqualified) -- and Haskell promises derived `Read`/`Show` are inverses. Rust `#[derive(Debug)]`: `Point { x: 0, y: 0 }`, bare. Python dataclass `__repr__`: `InventoryItem(name='widget', ...)`, bare. Java record `toString()`: javadoc says only "the name of the record class" and explicitly disclaims parsing ("subject to change ... should not be parsed by applications to recover record component values"). **Consensus: both languages that promise round-trippability chose the unqualified name anyway.**
     - **The two directions are not equally costed.** "Strip to the last dot-segment" is a helper call in the two `deriving.sprout` emitters. "Qualify everywhere" is *not implementable as stated*: the bundler qualifies per `module` header and an entry file has none, so uniform qualification would first require inventing a module identity for entry files -- a name-resolution change, not a fix.
     - **Recommendation: strip.** Needs a `stdlib/compiler/` edit, so it is seed-gated (`just refresh-seed`) plus a cross-module `to_string` test. `tests/stdlib/test_imported_records.spr` deliberately asserts only the field rendering (`ends_with "(x = 1, y = 2)"`) so that it does not bake in either side of this decision.
   - [x] `P2` **Parametric-record construction type-arg inference. LANDED 2026-07-31.** `infer_record_fields` no longer hardcodes the result as a bare `TConst`: `infer_record_expr` instantiates the record's declared type params ONCE (recovered from any field scheme's qvars — `make_record_field_scheme` generalizes every field over the full ordered list), threads that shared subst into each field's declared type instead of a per-field `instantiate`, and emits `TApp(TConst Name, [params…])`. So `Box(val = 5, …)` now types as `Box Int` and a `Box Int`-annotated use type-checks; field access/update already peeled `TApp`, so they light up unchanged; codegen is untouched (parametric fields already box uniformly, kind `'_'`). Monomorphic records are byte-identical (Nil qvars → empty subst, bare `TConst` result — seed fixed point holds at iteration 2). Also closed a **latent unsoundness** the old code masked: `Pair(fst = 1, snd = "x")` for `type Pair a = (fst: a, snd: a)` was wrongly accepted (each field instantiated independently); now rejected at construction. Tests: `tests/stdlib/test_parametric_records.spr` (incl. a parametric record inside an existential — the motivating `Widget` shape), `tests/conformance/type_error/record_parametric_field_conflict.spr`.
   - [x] `P3` **Inline function-field call `v.f(x)`. LANDED 2026-07-31.** A call whose callee is a dotted field access on an in-scope value (`v.render(x)`) parsed the callee as a whole (qualified) name → "Unknown variable v.render". `infer_call` now rewrites such a callee to a `GetFieldExpr` chain and infers it as a general higher-order callee (`dotted_field_callee`, mirroring `infer_var_or_field`'s head-first/value-wins rule — a module-qualified callee is unaffected); `ast_to_ir`'s `translate_call` `TGetField` arm loads the field's closure and applies via `IRApplyClosure` (same shape as the chained-call `TCall` arm). The field-loaded closure is heap-rooted across allocating arg eval — gated by a new case in `tests/stdlib/test_stress_records_heap.spr` (function field called with an allocating arg under `SPROUT_GC_STRESS=1`). Tests: `tests/stdlib/test_record_field_call.spr`; unblocks the clean-inline-call `examples/existential_widget.sprout` (parametric record + existential). Spec §5.6.3.
   Deferred (own designs, additive): dot-access "Scope B" (postfix `.field` on arbitrary expressions, needs `.` as an operator token); generic `Dict k v` with arbitrary key types (orthogonal to records); Elm-style first-class `.field` accessor functions for point-free pipelines.
   First milestone constraints: no row polymorphism, no structural subtyping, no implicit field punning, no field defaults/partial construction, no `deriving` on records, and no attempt to fold records into the current ADT surface without a dedicated spec.
6. Add a Unicode `Char` type and define string text semantics in v1.
   Initial scope: distinct `Char` values and literals, `String` text defined in terms of Unicode code points, and a small helper surface such as `char_at`, `char_at_or`, `string_from_char`, and `string_chars`.
   Landed initial slice: distinct `Char` type/literals, code-point-based string `length`/`slice`/`find` behavior across interpreter and native execution, and `stdlib.string` helpers `char_at`, `char_at_or`, `string_from_char`, and `string_chars`.
   First milestone constraints: code-point indexing/length/slice semantics only, no grapheme-cluster-aware APIs yet, and no promise of full Unicode normalization or one-to-many case mapping in the initial slice.
7. Add fast `Vec` sorting helpers in v1.
   Landed initial slice: `Ord`-constrained `vec_sort(vec)` and `vec_sort_by(key, vec)` helpers.
   Current built-in coverage: `Ord Int`, `Ord Bool`, and `Ord String`.
   Planned next slices: add constrained instance support first, then use it to define tuple `Ord` instances for composite sort keys such as `(Int, String)`.
   Remaining follow-up: decide whether the long-term design wants a richer public ordering story such as `Eq`, an `Ordering` ADT, constrained tuple instances, or custom descending/comparator APIs.
   First milestone constraints: keep the API `Vec`-focused, keep ordering fully inside the type system, and defer richer ordering surface area until the broader class design is clearer.
8. Add a minimal `Show` typeclass slice for library-friendly value formatting.
   Initial scope: `Show.to_string(x) -> String` with `Int`, `Bool`, and `String` instances, backed by a small host primitive for integer formatting.
   First milestone constraints: no promise that `print` uses `Show`, no interpolation syntax, no collection/ADT deriving, and no debugging-vs-user-display split yet.
9. Generalize experimental `do` notation toward real monadic sequencing.
   Current implemented scope: layout-style `do` blocks with `<-` binds, resolved type-directed for `Maybe` and `Result`, then lowered by a dedicated post-typecheck elaboration step through a small explicit core expression form into nested `match`.
   Completed compiler-architecture follow-up: `do` desugaring no longer stays entangled with the typechecker.
   Completed language follow-up: the experimental `do` surface now supports `IO`, pure local `let` steps, and narrow mixed `IO` plus inner `Maybe`/`Result` sequencing. That narrow mixed shape is now the preferred near-term story for multi-step `IO` and mixed failure-aware flows, with `after(...)` left as a compatibility convenience only.
   Planned next follow-up: prune remaining helper-heavy call sites that still obscure the `do` story, and only revisit broader sequencing abstractions if real code shows the narrow model is insufficient.
   First milestone constraints: keep current `Maybe`/`Result` behavior stable, keep mixed `IO` sequencing intentionally narrow, and prefer explicit `match` over speculative generalization when code needs the whole container value.
10. Add an `Alternative` typeclass for first-success chaining.
   Motivation: self-hosted parser combinators (Phase 6 self-hosting) need a principled "try this, else try that" primitive. The current workaround is a list-based `try_ops` helper in `stdlib/compiler/lexer.sprout`.
   Initial scope: `class Alternative f { fn alt(left: f a, right: f a) -> f a }` with a `Maybe` instance. A `<|>` infix operator would make call sites readable but requires tokenizer and parser changes (new 3-char token not currently in the language).
   First milestone constraints: `Maybe` instance only to start; decide on `<|>` operator vs named `alt` before widening; no `Applicative`/`Functor` hierarchy requirement in the initial slice.
11. Add algebraic effect handlers (phase 1: one-shot linear handlers).
   Design doc: [effect-system-handlers-draft.md](./docs/effect-system-handlers-draft.md).
   Motivation: eliminate explicit `TestState` threading in stdlib tests and establish the
   handler infrastructure that richer effect patterns (async, generators, capability
   injection) will build on.
   Initial scope: `effect` declarations, `handle`/`with` expressions, implicit perform,
   multi-label effect rows (`!{IO, Test}`), one-shot linear codegen via handler-record
   passing (no heap continuations or setjmp).
   First milestone constraints: one-shot handlers only (no multi-shot resumption), no
   open effect row polymorphism, no constrained effect operations, backwards-compatible
   (old `TestState` API stays in `stdlib/test.sprout` alongside new `run_tests`).
12. Revisit `|>` multi-arg semantics.
   Currently `x |> f(a, b)` desugars at parse time to `f(a, b, x)` (value appended as last
   argument). This gives the operator two distinct modes (plain-identifier RHS vs. partial-call
   RHS) that are not compositionally obvious. Options: drop the multi-arg form and require
   explicit lambdas (`x |> fn(y) = f(a, y)`), adopt Elixir-style value-first (`f(x, a, b)`),
   or keep as-is. First-class use is already covered by `pipe_apply` in the prelude.
   **RESOLVED 2026-07-26 (Package C-b).** Bare multi-arg `x |> f(a, b)` keeps the append form
   (`f(a, b, x)`, data-last). The `_`-placeholder partial (Current Priorities item 9 / part 1,
   landed) is the explicit way to place the piped value at a non-final position:
   `x |> f(_, b)` = `f(x, b)`, `x |> f(a, _)` = `f(a, x)` (append and hole-fill coincide only
   when the hole is last). The "two-mode / not compositionally obvious" concern is retired —
   position is now explicit via `_` rather than implicit. Normative in `spec-v0.md` §5.5;
   rationale in [currying-and-pipe-decision-v1.md](./docs/currying-and-pipe-decision-v1.md).
13. Add logging, debugging, profiling, and introspection to the self-hosted compiler (future).
   Design doc: [observability-guard-rails.md](./docs/observability-guard-rails.md).
   These features are not scheduled, but the design constraints in that doc must be respected in all Stage 2+ self-hosted compiler code so they remain practical to add. The six constraints — source locations first-class, explicit typed passes, explicit capability passing, no premature pass fusion, type survival into typed core, accurate effect annotations — are active guard rails, not future work items.
14. Add a source-level debugger for compiled user programs (v1).
   Design doc: [debugger-v1-draft.md](./docs/debugger-v1-draft.md).
   (Related but narrower: the stack-overflow-diagnostic-v2 item in "Tooling and Developer UX" above also proposes DWARF line-table emission for backtrace→source mapping. This is the broader `--debug` debugger.)
   Approach: emit LLVM DWARF debug metadata from the codegen pass (opt-in via `--debug`
   flag), then use `lldb`/`gdb` as the debugger UI.  Every `TypedExpr` already carries
   `SourcePos(index, line, col)` — the foundation is in place.
   Three milestones:
   - M1: DWARF emission in `codegen.sprout`; 4th IR section for debug metadata; `--debug`
     flag wired through the compile driver.  Delivers `b file.spr:N`, `n`, `s`, `bt` in
     `lldb` at Sprout source granularity for user-module functions.
   - M2: Extended `SproutCtorMeta` with `field_kinds` descriptor; ADT pretty-printer tool
     under `tools/` (implementation language — LLDB Lua / standalone C binary / format
     strings — decided at M2 kickoff).  Delivers human-readable ADT values at
     breakpoints.
   - M3: `just build-debug` / `just debug-run` recipes; `docs/debugging.md` §Debugging compiled programs section.
   First milestone constraints: debug metadata is strictly opt-in; release builds are
   unchanged; M1 scopes `!dbg` to user-module functions only (not stdlib/prelude) to
   avoid misleading source attribution in multi-file bundles; full multi-file DWARF is a
   post-M1 follow-up.
15. `wrap` ergonomics follow-ups (the v1 `wrap` keyword shipped 2026-06-13).
   The zero-cost distinct-type feature `wrap Foo = T` is normative — see
   `docs/spec-v0.md` §5.6.1.  These ergonomics improvements remain open:
   - Parameter-level destructuring: `fn f(Foo x) -> ...` desugars to
     `match arg with | Foo x ->`; useful for all single-constructor types,
     not just wrap.
   - Auto-generated zero-cost accessor: `wrap Foo = T` generates
     `fn foo_inner(Foo x) -> T = x`.
   - Named-field variant (longer-term): `wrap Foo { inner: T }` for named
     accessor syntax.
   - `opaque type` for Scala 3-style module-boundary transparency
     (transparent within defining module, opaque to callers). Distinct from the
     already-shipped `export type Name` export-opacity feature.
16. Span remaining error sites (guideline #5 Phase 3, tail).
   Landed on master: `Diagnostic`, `InferErr`, `TypedErr`, `CheckErr`, `BodyErr` carry
   `SourcePos` (Phase 1+2); lex/parse/type errors print `line:col: ERROR: msg`.
   PR #286: `BundleErr` spanned (via `ast.decl_pos` + validator threading) and the
   vestigial `LowerResult` collapsed. Branch `fix/span-pattern-inference`: all
   pattern-inference errors spanned (`ast.pattern_pos` + threading through
   `check_pattern_type`/`infer_tuple_pattern`/`check_pattern_list`/`infer_ctor_pattern`),
   plus branch-unify / fn-body / instance-method return-mismatch sites via
   `typed_expr_pos`, and `no_pos()`/`dummy_pos()` consolidated to a single guarded
   `no_pos()`. Branch `fix/span-call-resolve`: `infer_call_resolve`'s three `CallErr`
   sites spanned — a `pos` param threaded in from both callers (`infer_call_inner`'s
   call-expr pos and `eq_via_class_method`'s pos), so an argument-type mismatch now
   reports the call site's line (`g("hello")` → `7:14: … Call type mismatch`, was 0).
   Branch `fix/span-decl-validators`: the four `typecheck_decls` validators
   (`validate_all_decls` / `validate_entrypoint` / `check_overlapping_instances` /
   `check_missing_superclass_instances`) each widened from bare `Maybe String` to
   `Maybe (source.SourcePos, String)`, wrapping the error with `ast.decl_pos(decl)`
   at the outer scanner's seam (inner helpers keep `Maybe String`) — the #286
   `validate_deriving_decls` recipe. An unknown ctor-field type, a bad `main`
   signature, an overlapping instance, or a missing superclass instance now report
   the offending declaration's line, not 0.
   Remaining `no_pos()` *error* sites, each needing its own threading:
   - codegen / IR-emit errors (`IrLinesErr`).
   `typecheck_decl`'s `BodyLenient` internal-invariant arm intentionally stays `no_pos()`
   (unreachable-by-construction; no meaningful source position).
17. Add `stdlib.path` as the canonical Path API (v1).
   Design doc: [stdlib-path-v1-draft.md](./docs/stdlib-path-v1-draft.md).
   (Overlaps the proposed `stdlib.os` module in "OS and Process Primitives" above, which
   lists future `path_join`/`path_exists`; `stdlib.path` is the more-designed successor.)
   Motivation: PR #40 introduced `wrap FilePath`/`wrap StdlibRoot` for the
   compiler's swap-bug class, but the underlying path-construction is still naive
   `str_concat` joins in `module_loader.module_name_to_path` and
   `bundler.prelude_path` (latent trailing-slash and empty-root bugs). There is no
   stdlib Path surface today, so any future user program that touches the
   filesystem will re-invent join/parent/extension logic by hand. Designing
   `stdlib.path` now is cheaper than retrofitting after users depend on raw
   `String` paths.
   Initial scope: two zero-cost wraps `File` and `Dir` (extending PR #36's `wrap`
   philosophy to the stdlib boundary), pure ops `dir_file` / `dir_sub` / `*_parent`
   / `*_basename` / `file_extension` / `file_with_extension` / `*_normalize`,
   smart constructors `file_checked` / `dir_checked` rejecting empty + NUL, and
   migration of `read_file` / `write_file` / `*_exists` / `dir_list` to take
   `File` / `Dir`. Compiler-internal `FilePath` / `StdlibRoot` retire to
   `path.File` / `path.Dir`.
   First milestone constraints: POSIX-only (no Windows separator/drive-letter
   abstraction), no absolute-vs-relative type distinction, no eager
   normalization, no symlink resolution, no byte-level (OsString-style) paths.
   The POSIX-only constraint is unblocked by §10's Milestone B, not by
   Milestone A — A ships a Windows runtime without running the compiler there,
   and Win32 accepts forward slashes, so paths stay non-load-bearing until B.
18. **Scoped type variables (signature tyvars visible in bodies) — deferred, no current demand.**
    Analysis: [scoped-type-variables-analysis-2026-07-26.md](./docs/scoped-type-variables-analysis-2026-07-26.md).
    Sprout has no `ScopedTypeVariables` equivalent, and the feature does not apply
    today: implicit HM quantification (no user `forall`) means no lexical binder,
    and there are no local type annotations (`spec-v0.md:124`) for a body `:: a`
    to reuse. An audit of `stdlib/`+`stdlib/compiler/` found zero code that needs
    to pin a return-polymorphic type inside a body and cannot — `mconcat`/`empty`/
    `from_ordinal` all resolve via a sibling occurrence or are machine-generated.
    Hard prerequisite: local type annotations must land first. Realistic trigger:
    a binary serialization / codec library (a `decode : Bytes -> Maybe a`-style
    method consumed in isolation) — ties to candidate #4 and §2.5. Revisit only
    then; not deliberated on its own merits to date (only cited as GHC
    extension-sprawl evidence, `haskell-lessons-learned.md` §10).

### Self-Hosting Follow-Ups (post-M7)

The escape-Python effort is complete — milestones M1–M7 delivered, stage-3 fixed point
reached 2026-05-17. The detailed milestone execution log and the former
`docs/self-hosting-eliminate-python-backlog.md` (Phase 0–12 history) have both been
retired; see git history. Genuinely-open, non-Python follow-ups that outlived it:

- **Incremental build caching / invalidation.** No incremental compilation or module-graph
  invalidation support yet. Overlaps the stdlib-test-runner compile-cache follow-up under
  Current Priorities item 6 — a shared invalidation strategy could cover both.
- **Release-trust policy for self-built artifacts.** No release process defined for trusting
  binaries produced by the self-hosted compiler.
- **Stage-3 fixed-point in CI.** M7 stage-3 fixed point is verified locally; CI does not yet
  cache/produce the stage-2 binary to automate it. Likely already satisfied in spirit by CI's
  `just verify-bootstrap-fixed-point` seed gate — confirm coverage before scheduling separate work.
- **Formal minimum-language-subset spec (low priority, docs-only).** The self-hosted compiler
  uses the full language surface; no restricted bootstrap subset was needed, so a formal subset
  specification was never written.

## Compiler Internals Follow-Ups

- [ ] `P1` **`unifier.apply_full_subst` does not terminate on a cyclic substitution**
  (2026-08-18). Found via the LSP: `sproutd` sat at 99.7% CPU for 15 minutes with RSS flat at
  2.4 MB. `sample(1)` stack: `infer_call_var → unifier.instantiate_with_vars →
  apply_full_subst → apply_full_subst → …`, dominated by `map_get_unboxed`/`strcmp`. Flat RSS with
  unbounded time means it is cycling a fixed structure rather than recursing away — consistent with
  a binding `α := … α …` that an occurs check should make impossible, though that is NOT yet proven
  (re-expansion of a shared substitution chain fits the evidence too).
  **Trigger, bisected:** two `task_fork`s whose forked function calls any *imported-module*
  function. One fork is fine; two forks calling only prelude builtins are fine; two forks calling
  `task_sleep` OR `string.trim` both hang, so the effect/`Task` machinery is a red herring and the
  imported-scheme instantiation is the common factor. Minimal repro is 18 lines — see the trigger
  table in `docs/module-surface-authority-v0.md` §7.1; `examples/concurrent_fetch.sprout` is the
  real-world case, which the bundler checks clean in 0.28s.
  **Now unreachable from editors** (the env path that reached it is retired), so this is latent, not
  live — but it is a real non-termination in the unifier and other routes to it are unproven. Any
  fix needs a time-bounded harness: an in-process `.spr` test cannot bound its own runtime and would
  hang `just test` instead of failing it.

> Open compiler/runtime follow-ups relocated here 2026-07-05 from the retired
> "Minimum Viable Path to Escape Python" milestone log. Milestones M1–M7 completed
> (stage-3 fixed point, 2026-05-17); the done-history execution log was dropped —
> see git history. Items below are grouped by theme; text is verbatim from the log.

### Sprout-IR / Model-C Codegen

These are the granular implementation follow-ups for the "Execute Model C GC-rooting
plan" item under "Design Roadmap → Current Priorities".

- [x] `P1` Sprout-IR: Tier-1 CPR peephole — unbox `match` over a Maybe-returning C extern (2026-07-09). The active codegen path had zero unboxing (the typed-codegen flip left CPR behind on the retired `codegen.sprout`), so every immediately-matched `vector_get`/`str_char_at`/`map_get`/… allocated a `Just` box. Measured: the box + strip is ~71% of the get cost (~20 ns of ~28 ns; a boxed `match vector_get` loop is 22.4 ns/iter vs a 2.7 ns raw-read floor). **Landed**: a new `IRCallUnboxed2 <tag> <val> <val_kind> <fn> <args>` op (`sprout_ir.sprout`) lowering to `call { i64, i64 } @<fn>_unboxed` + two `extractvalue`s; a dedicated peephole in `translate_match` (`ast_to_ir.sprout`) that fires when the scrutinee is a direct call to one of the 10 Maybe-returning externs (runtime `_unboxed` variants already existed) and every arm is `Nothing`/`Just <var|_>`/`_`; ir_rooting handles the op (`op_triggers_gc`=true, `op_produces_simple_heap`→`val` per kind); 10 `{ i64, i64 }` declares in `ir_header`. Measured **6.5× on the direct-extern-match loop** (22.4→3.5 ns); fires 35× in the compiler self-compile (17 on the lexer's `str_char_at`). Does NOT touch the shared arm machinery — boxed matches are byte-identical. Tests `test_unboxed_maybe_extern.spr` (IR-shape) + `test_unboxed_maybe_runtime.spr` (behavior: payload = extractvalue 1, tags not swapped).
- [x] `P1` Sprout-IR: Tier-2 CPR — unbox regular Sprout functions that return a width-2 ADT (`max_ar ≤ 1`: `Maybe`/`Result`/custom `Empty | Full a`) immediately matched OR do-bound (2026-07-10). Generalizes Tier-1 (10 C externs) to any top-level Sprout fn: a `{tag,val}`-returning `@<name>_worker` is emitted for every matched/do-bound callee (total emission → local routing, no threaded eligibility set); the boxed `@<name>` is kept for escaping uses. **Worker body** (`translate_tail_unboxed`, `ast_to_ir.sprout`): tail ctor → `IRRetUnboxed2`; `if`/single-arm `match` → recurse; tail-call to a Maybe-extern → chain `@<fn>_unboxed`; tail-call to another worker → chain `@<g>_worker` (self stays boxed for TCO); anything else → boxed catch-all repack. **Routing**: `unboxed_maybe_match_target` (for `match`) + `translate_do_bind_maybe`/`_result` (for `x <- f()` do-binds — the recognizer's actual path). **The active codegen path is `ir_pipeline.compile_program_streaming` (`stream_workers`), NOT `translate_program`.** Emitted workers are streamed through `emit_one_fn` so they get rooting (gated on `just test-stress` ✓) + string-const handling. Self-hosting fixed point verified (stage-2 == stage-3). **Perf**: the real digit recognizer (MutMatrix/MutVec Double getters via do-bind, 30 epochs) goes **~22.9s → ~2.9s ≈ 8×**, identical accuracy — the per-get `Maybe`/`Result` box was driving GC pressure in the mixed-allocation training loop. (Tight-loop microbenchmarks understate this: an isolated same-cell get loop reclaims young boxes near-free and reads ~neutral; trust the end-to-end number.) Width-3 sret for `Maybe (A, B)` payloads remains a further step (see `docs/cpr-nested-product-unboxing-plan-2026-06-29.md`).
- [ ] `P3` **CPR for bare-type-variable results — analysed 2026-08-12, re-scoped; the originally filed design is withdrawn.** Requested `P1` on 2026-08-12 as "restore CPR for generics", on the assumption that the §1 ABI bugfix had switched CPR off for generic functions. **It had not.** The gate declines *bare* type variables only; a generic declared `-> Maybe b` or `-> Box b` resolves its head through `type_head_name` and still workerizes with the constructor fully fused. Verified by emitting IR for a probe file — `fn known_head(x: b) -> Maybe b = Just(x)` produces a worker with zero allocations, two `insertvalue`s and a `ret`:
  ```llvm
  define { i64, i64 } @main.known_head_worker(i64 %x) {
    %t$0$r0 = insertvalue { i64, i64 } undef, i64 1, 0
    %t$0$r1 = insertvalue { i64, i64 } %t$0$r0, i64 %x, 1
    ret { i64, i64 } %t$0$r1
  }
  ```
  while `fn bare_tyvar(x: a) -> a = x` stays `call i64 @main.bare_tyvar`. So CPR for generics already works; the residual gap is the bare-tyvar case alone.
  **Why the residual gap cannot pay, and why the filed design is withdrawn.** By parametricity a function declared `-> a` cannot *construct* its result — nothing of type `a` typechecks as a constructor application. It can only pass a value through or forward it. But Tier-2 CPR's allocation win comes exclusively from fusing a tail constructor into the unboxed return (the `insertvalue` pair above); the pass-through path in `translate_tail_catchall` emits `translate_expr` to obtain an already-boxed value and *then* `IRGetTag`s it. So the bare-tyvar population is structurally the population with nothing to fuse, and any worker emitted for it relocates two loads from caller to callee: **approximately zero gain, plausibly slightly negative** from the extra symbols. (Carve-out: a `where C a` constraint lets a body *obtain* an `a` via a method call such as `mempty`, but the construction happens inside the instance method's body — the generic body's tail is still a call, so no fusion happens in the generic worker either.) The withdrawn design was per-instantiation symbol specialization: route to `@f_worker__<head>` off the call site's `call_ty`, emitting one worker per (callee, instantiated head). The mechanism is sound and call-site-local — the router (`unboxed_maybe_match_target`) and `collect_wc_scrutinee` both see `call_ty`, so both derive the same symbol with no threaded eligibility set — it is simply aimed at a population that cannot benefit. Two sibling boundary-only designs are withdrawn with it: a single ADT-convention worker doing a runtime arity read (also needs a new builtin, so it is dominated), and unifying the two unboxed conventions so `@f_worker` has one meaning.
  **The `list_fold` example that motivated the original entry was wrong.** Its result is the accumulator, a boxed value threaded through boxed *parameters*; CPR unboxes results, not parameters, so no allocation disappears there regardless of what the router decides.
  **Correction to the §1 cross-reference:** the conservative gate the bugfix installs is **not** scaffolding to be removed when this lands — under this analysis it is the permanent correct behaviour for bare-tyvar heads.
  **The measurement that motivated the entry stands, but measures something else.** `clang -O2`, 20M calls, monomorphic A/B: 0 allocations / 1 GC cycle / 0.01 s unboxed vs 20 000 000 allocations / 4 883 GC cycles / 0.33 s boxed ≈ **16 ns + one box per call** (with the box gone LLVM folds the loop to arithmetic, so the ratio overstates the allocation itself). That is the price of *a box*, and it is real — it is not the price of this gap, because the bare-tyvar population never allocates a box that CPR could have removed. The GC-cycle count suggests collection pressure, not allocator latency, dominates.
  **What is still on the table**, if that per-box price is worth chasing: (a) inlining small generics *before* the CPR router runs, so the result type is concrete when the router looks — the repo already has IIFE-inlining machinery from the mutual-TCO arc, but this misses recursive callees; (b) specializing the *body* per instantiated head (monomorphization-lite), which conflicts with type erasure and separate compilation; (c) attacking allocation cost directly rather than the ABI — see the sibling `P3` on allocator attributes. These are different work from this entry; re-file under whichever is chosen rather than reopening this one. Priority moved `P1` → `P3` because the re-scope shrank it to a no-payoff item; that call is Kuba's to reverse.
- [ ] `P3` **The emitted LLVM `declare`s carry no attributes, so every allocation is opaque to the optimizer.** Observed 2026-08-12 while analysing the CPR-for-generics gap. In `bootstrap/compile_driver.ll` the only annotated extern in the whole seed is `declare void @sprout_abort_match() noreturn`; `declare i64 @sprout_alloc_obj(i64, i64)` and every other runtime import are bare. LLVM therefore models each allocation as may-read-write-all-memory and may-not-return, which blocks dead-allocation elimination, load forwarding across allocations, and heap-to-stack promotion outright. Adding `allocsize`/`willreturn`/`noalias`/memory-effect attributes in `ir_header` is a small, broadly-scoped change that would help every boxed path, not just CPR's. **Not a free lever, and needs its own feasibility note before any code:** stack promotion is entangled with precise-GC rooting — a rooted pointer escapes into the shadow stack, and an unscanned stack copy violates the scan invariant — so the safe subset of attributes has to be established first, most likely excluding anything that authorizes promotion or elimination of a rooted allocation.
- [ ] `P3` Tier-2 CPR follow-up: bare-name `adt_index` collision (code-review finding, PR #157). `build_adt_ctor_index` keys by the bare type name, and every reader uses the bare `type_head_name`, so two modules declaring width-2 types with the same bare name collide last-write-wins; a catch-all repack then uses the wrong `(tag, arity)` list and can emit `IRGetField(box, 0)` on a genuinely nullary ctor → reads adjacent heap into a rooted `val` = silent corruption (not a loud abort). Amplifies the pre-existing bare-name type-identity collision (sproutd's duplicate `Diagnostic`); a real fix is qualified type identity in the type system, not adt_index alone. Also: the qualified `adt_index` key `build_adt_ctor_index` inserts is currently DEAD (never read) — delete it or make the reader disambiguate via it.
- [ ] `P3` Tier-2 CPR follow-up: unify the two worker-emission shells (code-review finding, PR #157). The active streaming path (`ir_pipeline.stream_workers` + the inline `collect_worker_callees`/`build_adt_ctor_index` in `compile_program_streaming`) and the test-only batch path (`ast_to_ir.tier2_augment_fns`/`emit_all_workers`, reached only by `translate_program` in the IR-shape tests) duplicate the same collect+index+per-callee-emit+idx-merge loop with different accumulator plumbing (`StreamState` vs `(fns, idx_map)`). The per-callee core (`emit_worker_for`) is shared; only the loop/merge shell diverges — so IR-shape tests exercise a different shell than production ships, risking drift. Extract one shared loop. (Same-area minor efficiency: `worker_source_for` re-scans all decls per callee, O(N·D) — build a name→decl `Dict` once; `build_adt_ctor_index_go` computes `adt_ctor_entries` twice per TypeDecl — bind once.)
- [ ] `P2` **Tuple-return CPR does not fire on a SELF-RECURSIVE call, so a recursive tuple-returning reduction boxes once per step.** Measured 2026-08-06 while deciding whether `stdlib/math.sprout`'s reductions should return `(mantissa, exponent)` instead of threading an accumulator. CPR (`docs/scalar-replacement-v0.md`, Stage 1 width-2/3) fires correctly on the OUTER call: a `where`/`let`-destructuring caller of a top-level tuple-returning fn gets `{i64, i64}` back by value with no allocation. It does NOT fire on the function's own recursive edge — the worker calls the BOXED wrapper, which does `call i64 @sprout_alloc_tuple_blob(16)`, then the worker reloads both fields and repacks them. So each recursion step costs one heap tuple plus an unpack/repack round-trip, and the self-tail-call that TCO would otherwise turn into a loop is lost. **Cost, measured A/B in one binary over a 2M-call sweep with bit-identical results: `ln` 4.3 ns/call accumulator-threaded vs 12.4 ns/call tuple-returning — 2.8x — and ~29.5 ns/call on a cold heap, the difference being GC.** Re-runnable: `bench/math_transcendental/accumulator_vs_tuple_bench.sprout`; write-up in `bench/results-2026-08-06-math-transcendental.md`. Both halves are pinned by `tests/stdlib/compiler/test_tuple_return_cpr.spr`, whose "KNOWN GAP" assertion flips from `assert_true` to `assert_false` when this is fixed. **Consequence today:** `sqrt_reduce`, `ln_reduce` and `cbrt_reduce` thread an accumulator, which reads as a workaround but is the faster shape — documented in `stdlib/math.sprout` under "Why the reductions thread an accumulator". Fixing this makes the tuple form free and would let those three be written in the more legible decomposition style; it should also help any recursive tuple-returning function in the tree, so the payoff is not limited to `math`. Likely shape: let `translate_tail_unboxed`'s worker chain a self-recursive tuple return to `@<self>_worker` rather than falling back to the boxed repack — the same trick Tier-2 CPR already applies to a tail-call into ANOTHER worker (`@<g>_worker`), where self-calls were deliberately left boxed to preserve TCO. That TCO caveat is the thing to resolve: here the self-call IS the tail call, so the fix must keep it a tail call rather than trade one cost for the other.
- [x] `P1` **`loud-fail-smoke` is RED on master and guards a deleted code path. FIXED 2026-08-07.** Rewritten to probe a name that is neither a runtime builtin nor a compiler intrinsic, to assert the diagnostic appears AND that no IR is emitted (the actual zero-fill regression), and to carry a POSITIVE CONTROL so "no IR" cannot pass vacuously if the compiler ever stops emitting IR at all. All three assertions were verified to FIRE on a violated invariant before being trusted. It reads BOTH streams deliberately, so it asserts the *diagnostic* rather than which stream carries it. **One claim below turned out to be wrong and is worth keeping visible: the "There is no soundness hole … still rejected loudly" reassurance was false as stated.** The diagnostic *is* produced, but it went to **stdout with exit 0**, which no caller can observe as rejection — so the premise was broken in a *third* independent way beyond the two identified here, and `if <compile>; then fail` could never have fired regardless of the grep string. That defect is fixed separately (see the driver diagnostic-stream entry) and is now guarded by `just diagnostic-stream-smoke`. Lesson recorded in `docs/compiler-internals.md` §"Driver diagnostic contract": the whole negative-test surface is exit-status-blind by construction (`_test-reject` runs `2>&1 || true`), so 244 green suites said nothing about exit status — when a signal is deliberately ignored everywhere, gate it *specifically*. Original diagnosis follows. Root-caused 2026-08-06 (it failed while running the DoD battery for an unrelated change; verified pre-existing by running master's own compiler binary, which behaves identically). The gate compiles `print(int_to_string(5))` with no imports, asserts the compile FAILS, and greps stderr for `"unresolved call"`. Every part of that premise is now wrong: (1) the string `"unresolved call"` exists **nowhere** in the tree except the gate's own body — it was a `panic` in `emit_named_call` at `stdlib/compiler/codegen.sprout:2601`, and **that file was deleted** when direct codegen was retired; (2) `int_to_string` is a builtin whose `declare i64 @int_to_string(i64)` is emitted unconditionally by `ir_header`, and `print` is a compiler intrinsic (already documented in the `print`/`ToString` redesign entry above as the reason importless `print` resolves), so an importless call to either is *by design* resolvable — the program compiles, links, and correctly prints `5`. **There is no soundness hole:** a genuinely undefined callee is still rejected loudly (`totally_undefined_fn_xyz` → `ERROR: check: Unknown variable`), and builtins are genuinely type-checked (`str_len(5)` → `ERROR: check: Call type mismatch: String vs Int`). Preferred fix is to **preserve the gate's intent rather than delete it** — keep asserting "no silent zero-fill for an unresolvable call", but use a name that is not a builtin, and grep for the message the IR path actually emits (`ERROR: check: Unknown variable`) instead of the retired panic text. Deleting it outright loses a real regression guard for the strictness that replaced `zero_val`.
- [x] `P1` **`gate-audit` only checks one direction. FIXED 2026-08-07** as assertion C: every recipe in `gate`'s dependency closure must appear in CI's closure (`ci.yml`'s named tasks plus `ci-fast-gates`' own `GATES` array), with a documented `GATE_ONLY_EXCLUDE`. Comparison is by **closure, not name**, because CI runs umbrella recipes (`test`, `ci-fast-gates`) whose children it never names — a name-match version would have false-flagged `test-stdlib-stage1` and a dozen others. `EXCLUDE` needs exactly one entry, `gate` itself (self-reference: CI runs the constituents in parallel via `ci-fast-gates` rather than `gate` sequentially); the reverse diff was otherwise exactly `loud-fail-smoke`, so the assertion had no hidden tail. Verified RED on precisely that one recipe before wiring it into `ci-fast-gates`, GREEN after. Note `fmt-check` is *not* excluded — the original sketch below guessed it was local-only, but CI does run it. The assertion also fails loudly rather than vacuously if the `GATES` array cannot be parsed, since a silent parse failure would make it pass on everything. Worth noting the impact was worse than "a gate CI never runs": `gate` aborts at the first failure, so a red gate-only recipe silently truncated the entire *local* battery after it — here, everything past `loud-fail-smoke`, which is most of it. Original analysis follows. The audit asserts *everything CI runs is covered by `just gate`*. It does **not** assert the converse — that everything `gate` runs is also exercised in CI. `loud-fail-smoke` is in `gate`'s dependency list (`justfile:1673`) but absent from `ci-fast-gates`' `GATES` array, and `.github/workflows/ci.yml` invokes `ci-fast-gates` (plus `test-stdlib-*-stage1`, `compile-examples-stage1`, `test-stress`, `verify-bootstrap-fixed-point`) — never `gate` — so it has not run in CI and went red without any signal. This is the **same defect class** as the orphaned `ir_golden_diff.sh` fixed in PR #26; the `gate-audit` assertion added there guards orphaned `scripts/*.sh`, not gate-only recipes, so it structurally cannot catch this. Fix: add a reverse-direction assertion (every recipe reachable from `gate`, minus a documented EXCLUDE for genuinely local-only ones like `fmt-check`, must appear in `ci-fast-gates` or the workflow). **Sequencing matters:** landing that assertion turns CI red until the `loud-fail-smoke` entry above is resolved, so fix the gate first or land both together. Verify the guard *fires* before trusting it — a green meta-guard proves nothing.
- [ ] `P3` **`type_driver.sprout` and `lower_driver.sprout` are orphaned executables carrying a fixed defect — decide whether to wire them up or delete them.** Found 2026-08-07 while routing driver diagnostics to stderr. Both are `module main` executables with their own `main`, and both still report errors via `print` (stdout) — `type_driver.sprout:35,38,43,64` and `lower_driver.sprout:51,54,59,81` — the exact defect fixed in `compile_driver.sprout` in that change. They were left untouched **deliberately**: nothing builds them (no justfile recipe, no CI step, no script), and the only references anywhere in the tree are historical BACKLOG entries plus one stale comment at `checker.sprout:62` ("Used by lower_driver to feed the lowering pass"). Fixing an unbuildable binary would be an unverifiable change, which is worse than leaving it consistent-and-dead. Their original purpose was Python-parity diagnosis (`tools/dump_types.py`, `tests/test_checker_parity.py`) from before the Python compiler was removed, so the likely answer is **delete**, plus the `checker.sprout` comment. If instead they are wanted as `--phase`-style diagnostic entry points, note `compile_driver --phase check` and `--phase lower` already do what they do, so the case for keeping them needs stating. Either way `gate-audit`'s assertion B does not catch this — it guards orphaned `scripts/*.sh`, not orphaned `.sprout` executables, which is arguably a third direction worth adding.
- [ ] `P3` **No scientific-notation Double literal in the lexer.** `1.5e3` is a parse error (`Expected ) at 2:46`); so is any integer part above 2^63 (`18446744073709551616.0`). Consequence: constants like 2^64 / 2^512 / 2^-512 are **not writable as literals at all**, which is why `stdlib/math.sprout`'s power-of-two ladder is built from module-level `let`s and `sq()` calls rather than written directly — a forced shape, not a style choice. Adding `e`/`E` exponents to the number lexer would let the ladder be literals, which also routes them through `eval_const_expr_ir`'s const path (`private constant`, no startup store, no GC root). **Read the entry below before assuming that last part is a speedup — measured, it is not, on arm64.** Also worth deciding at the same time: whether hex-float (`0x1.8p3`) is wanted, which is the only exact-and-readable spelling for a power of two.
- [ ] `P3` **Do NOT re-attempt const-folding Double-literal globals as a performance fix — measured, reverted 2026-08-06.** Recorded so the analysis is not repeated. The mechanism is real: a module-level `let` becomes a *mutable* `global i64 zeroinitializer` written by `@__sprout_init_globals` whose address escapes to `@sprout_gc_register_i64_root`, so LLVM cannot fold it and keeps an `adrp`/`ldr` at every use. Routing `TFloat` literals to the existing `GlobalConst` path (emitting `@g = private constant i64 bitcast (double <text> to i64)`, which LLVM does fold to an immediate) was implemented and worked — `ln_reduce`'s `adrp` 65→26, `ldr` 86→41 — but it **lost overall**: `mov`+`movk` went 181→358 in the same function, a net **+163 instructions**, because the i64-uniform value ABI makes LLVM see an *integer* constant, and an arbitrary 64-bit integer immediate on arm64 costs `mov` + 3× `movk` = 4 instructions against `adrp` + `ldr` = 2 plus an L1 hit. Paired wall clock over 14 interleaved rounds put `ln` at median ratio 1.12 (slower) and paired user CPU never once faster. Two further dead ends: **LLVM removed floating-point constexprs** (`fdiv constexprs are no longer supported`), so arithmetic like `1.0 / two8` has no constexpr form; and folding it inside the compiler is blocked because that requires printing a computed Double back as a decimal literal, which `double_to_string` cannot do round-trip exactly. Full write-up: `bench/results-2026-08-06-math-transcendental.md` §"A third measurement that did not survive scrutiny". If revisited, the only promising variant is emitting the global as `double`-typed rather than `i64`-typed so LLVM applies its *FP* constant cost model — that needs `IRLoadGlobal` to know the global's type, and it must be measured, not assumed.
- [ ] `P1` Sprout-IR: Bool return and Bool capture in closures — requires `IRZextI1ToI64` + `IRTruncI64ToI1` (2026-06-06): `translate_lambda` in `ast_to_ir.sprout` now rejects (a) lambdas whose body type is Bool (lifted fn is always `i64` ret; a Bool body produces `i1` — LLVM verifier mismatch) and (b) lambdas that capture a Bool-typed outer variable (IRLoadEnvSlot returns `i64` but the source-level value is `i1`). Both cases need selective widening/narrowing ops: `IRZextI1ToI64` at the lifted body's `IRRet` for (a), and `IRTruncI64ToI1` after each Bool `IRLoadEnvSlot` for (b). Wire these as a follow-up alongside the ctor-field `IRZextI1ToI64` (see next entry). Surfaced by /code-review findings #1+#2+#3 on PR 2.4.
- [ ] `P1` Sprout-IR: add `IRZextI1ToI64` op so Bool ctor fields can lower (2026-06-05): the IR codegen path (`stdlib/compiler/ir_lowering.sprout`'s `IRMakeCtor` lowering, `--use-ir-codegen` flag) hardcodes `i64 %argN` for every ctor field slot. Bool expressions produce `i1`, and the legacy codegen.sprout has a `pack_to_i64` helper at ~line 960 that emits `zext i1 ... to i64` at the ctor call site — the IR path has no equivalent. PR 2.3's 2nd /code-review fix-batch (commit `3049854`) added a defensive `is_supported_ctor_arg_type` filter in `ast_to_ir.sprout` that returns `Err "Bool ctor field not yet supported (requires zext i1 to i64 — deferred to follow-up PR)"`. **The follow-up PR**: add `IRZextI1ToI64 String String` op to `sprout_ir.sprout` (result, value-name); wire `translate_args_ctor` to insert the op when the arg's typed type is Bool; emit `zext i1 ... to i64` in `lower_op`. Relax the filter once the op is wired. Same shape will be needed for Unit (likely a constant-zero IR op or a typecheck-time rejection — Unit has no LLVM value).
- [x] `P2` Executable-entrypoint signature is spec'd but never validated. **STALE — verified FULLY IMPLEMENTED 2026-08-07.** Every case this entry lists as "compiling silently" is now rejected with the exact message §10.10 promises, and each has a conformance fixture under `tests/conformance/executable_error/`: a `main` with parameters → `must take zero arguments` (`main_arity_mismatch.spr`); a non-`Unit`/`Int` return → `must return Unit or Int` (`stdlib_main_maybe_entrypoint.spr`); a pure `main` → `must declare the {IO} effect` (`main_pure_entrypoint.spr`); an effect-polymorphic `main` (`main_effect_polymorphic_entrypoint.spr`); and — **correction 2026-08-16** — `executable_error/main_int_entrypoint.spr` does *not* pin the sanctioned `Int` form despite its name: it declares `fn main() -> String !{IO}` and is a second *rejection* test for `must return Unit or Int`. The sanctioned `Int` form is pinned by `run/main_int_exit_ok.spr` (accepted, runs, exit code 0), noted correctly in the entry below. Confirmed by running the arity and return-type cases directly, not only by fixture presence. One genuine gap remains and is *already tracked as its own fixture*: `missing_main` is `xfail` — a program with no entrypoint at all still produces no diagnostic. Original report follows (2026-07-21): §10.10 requires `main` to be exactly `fn main() -> Unit !{IO}` or (as of the Int-exit-code fix) `fn main() -> Int !{IO}`, zero-argument, non-pure, non-effect-polymorphic — but no compiler pass checks any of this. `is_entry_fn_name` (`ast_to_ir.sprout`) only matches on the *name* (`main` or `<module>.main`); it never inspects arity, return type, or effect row. Concretely: a pure `main`, an effect-polymorphic `main`, a `main` with parameters, or a `main` returning some other type all currently compile silently instead of being "rejected at the executable boundary" as the spec promises. Discovered while fixing the Int-exit-code bug (main_shim discarding `main`'s return value) — `fmt_driver.sprout`'s `fn main() -> Int !{IO}` had been compiling for a long time despite the then-current spec only sanctioning `Unit`. Fix: add a validation pass (probably in `ast_to_ir.translate_program` alongside `validate_reserved_namespace`) that checks the entry-fn's arity/return-type/effect-row/purity against §10.10 and produces a clear diagnostic on mismatch.
- [ ] `P2` Sprout-IR: extract `main_shim` register_ctor calls into `@_sprout_ir_module_init()` (2026-06-05): PR 2.3 / commit `3b92d2c` modified `ir_lowering.sprout`'s `main_shim` to emit `@sprout_register_ctor(...)` calls before `ret i32 0`. The new shim is no longer a passive `ret i32 0` placeholder; it always emits an `@main` that depends on the runtime registration function. **Latent risk**: a future bundle-smoke or test-driver harness that wraps the lowered module with its own host `int main(int, char**)` will hit `duplicate symbol _main` at link time — the trivial old shim could be `#define`d or weak-attributed away; the new emission cannot. **The fix**: extract the registration calls into a dedicated `@_sprout_ir_module_init()` function (still emitted by `lower_program`), and either (a) have `main_shim` `call` it then `ret i32 0`, or (b) attach `__attribute__((constructor))` so the runtime invokes it before any user code regardless of main provenance. Latent — not blocking until bundle-smoke wraps an IR-lowered module. Surfaced by /code-review finding #10 on PR 2.3.
- [ ] `P2` Investigate trivial pattern-match-and-return codegen bug — duplicate `entry:` LLVM (2026-06-05): adding three tiny accessor helpers to `stdlib/compiler/ast_to_ir.sprout` (e.g. `fn ctor_tag(entry: (Int, Int, Int)) -> Int = match entry with | (t, _, _) -> t` and two similar) caused the legacy codegen (`stdlib/compiler/codegen.sprout`) to emit LLVM with a duplicate `entry:` block label deep in the stage-2 output. Both `just test` and `just refresh-seed` failed with `opt: ... error: unable to create block named 'entry'` at line ~130912 / ~226512 of the lowered file. The exact same shape exists elsewhere in the codebase (`codegen.sprout:1566`, `prelude.sprout:425`), so the trigger condition is subtle — possibly related to the helpers being mutually adjacent, all having tuple-of-Int as param type, or being called in close sequence. **Workaround in place**: PR 2.3 / commit `3049854` reverted those three accessors back to inline `match entry with | (tag, actual_ar, max_ar) -> ...` destructure at the two call sites; project memory `project_trivial_accessor_codegen_bug` records the empirical signature so future agents don't reintroduce. **The investigation**: build a minimal repro (likely 5-10 lines of Sprout source emitting duplicate `entry:` in lowering), bisect into `codegen.sprout` to find the offending emit path, fix. Once fixed, the deferred refactor of `ast_to_ir.sprout` ctor-table accessors can land.
- [ ] `P2` Capturing IIFE returning String fails to translate (2026-06-06): a capturing-IIFE source like `fn f(n: Int) -> String = (\\x -> int_to_string(x + n))(7)` fails in `ast_to_ir.translate_program` even though all the component pieces (capturing lambda, IIFE, int_to_string GC trigger, String return) work individually. Surfaced as T7a's precondition assertion failure during the /code-review round-3 fix pass. Suspected interaction between (a) the `name_prefix` threading added in round 3, (b) the lifted body's String return reaching translate_user_fn's `is_string_type` rejection path, OR (c) a Result-monad bind issue in the refactored translate_lambda helper chain. T7 (idx_map collision regression test) is deferred until this lands. The other capturing-IIFE tests (T3, T4, T6) use Int return and pass, so the regression is specifically around String returns from lifted bodies.
- [ ] `P2` Top-level let binding resolution in lambda bodies (2026-06-06): build_top_level_fn_set now includes TLetDecl names so compute_free_vars correctly excludes them from a lambda's free vars (round-4 finding #4 partial fix). But translate_expr's TVar arm has no handler for top-level let names — it falls through to Err("unbound variable"). Three options for full resolution: (a) emit each TLetDecl as a 0-arg `@<name>()` IRFunction and emit `call i64 @<name>()` at use sites — semantic concern for non-pure bodies (called every reference); (b) inline-translate the let body at each use site — clean for pure literals, work-duplication for complex bodies; (c) emit TLetDecl as an LLVM global with one-time init function (cleanest semantically, more codegen machinery). T20 in test_ir_codegen_closures.spr deferred until decision lands.
- [ ] `P2` Capturing IIFE returning String — subtler failure path remains (2026-06-06): round-4 finding #3 removed the obvious is_string_type rejection in translate_direct_call, but `fn f(n: Int) -> String = (\\x -> int_to_string(x + n))(7)` still fails to translate at a subtler step (possibly outer fn's return-type handling, or a propagation issue in translate_lambda_lifted's body-result type tracking). T21 deferred. Investigate by adding a debug println at translate_program's Err branch or by stepping through translate_expr on the source's TCall→TLambda→body chain.
- [x] `P2` Closure-call arity check is unreachable through Sprout source (2026-06-06) — **the premise was false, and was disproved 2026-08-14.** The entry read: "the typechecker rejects over-application before the IR layer sees it. The check lives as defense-in-depth." That holds only for a *named* callee, whose declared arity the checker knows. Applied to a function-typed **value** the typechecker rejects nothing, because a Sprout function type does not determine a value's arity — `\(x, y) -> …` and `\x -> \y -> …` both have type `Int -> Int -> Int`. Both mismatch directions were reachable from plain source and neither was diagnosed: over-application returned the closure handle as the result type at exit 0, under-application SIGSEGV'd. Closed by the runtime arity check in the entry under "Compiler / Stdlib Misc" above; `tests/closure_arity_smoke/` now holds the regression the deferred T17 was waiting for. The IIFE check in `translate_call` remains defense-in-depth for the case this entry actually described.
- [ ] `P3` **Re-run the B3 SIMD checkpoint now that B1-Double has landed.** The last B3 checkpoint (2026-07-12, run against B2-only, B1 not yet landed) disassembled the digit recognizer's row kernels (`row_dot_go`/`row_sub_scaled_go`/`row_add_scaled_into_go`) at `clang -O2`/`-O3` and found zero `.2d`/vector-lane ops anywhere in the binary, with `clang -Rpass-analysis=loop-vectorize` naming the blocker in its own words as "call instruction cannot be vectorized" (reproduced on the isolated kernel). That negative result was gated entirely on B1 (the per-element `vector_get_direct`/`vector_mutset` calls) not yet being inlined — it corrected an earlier assumption that the `tco_loop`/`stacksave` shape blocks LLVM's loop recognition; O2 asm showed SROA already promotes the alloca'd loop state to registers and forms a clean counted loop, the only residue being a per-iteration `mov sp` (stackrestore). B1-Double (inlining monomorphic `Vector Double` reads/writes as typed IR ops, landed 2026-07-12) removes those calls. Re-run the checkpoint — disassemble the three row kernels at `-O2`/`-O3`, re-check `-Rpass-analysis=loop-vectorize` — to get a definitive B3 verdict. If still blocked, the next suspect is the bounds-check panic branch B1 introduced (post-B1 checkpoint already saw the blocker shift from "call instruction" to "Incorrect number of successors from early exiting block"), which would need hoisting out of the loop. Note `row_dot_go` is a reduction and cannot SIMD-reassociate without perturbing the golden training output (139/150 accuracy) — only the independent-write kernels (`row_sub_scaled`/`row_add_scaled_into`) can vectorize bit-identically; `row_dot`'s SIMD path is separately gated regardless of this checkpoint's outcome. Design doc: `docs/phase-d-numeric-fastpath-design-2026-07-11.md` §B3.

### Linear types (Model C Milestone 4) follow-ups

M4.1 (parse + record `type linear`) and M4.2 (consume-exactly-once enforcement, with
M4.3 branch convergence merged) have landed. See `docs/linear-types-m4-scoping-2026-08-01.md`
and `docs/linear-types-m4.2-enforcement-2026-08-06.md`. Deferred, in the order they matter:

- [ ] `P2` **Higher-order linearity (M4.4) — the general case.** M4.2 loud-rejects a linear binding
  captured by a lambda and any linear lambda parameter (`linear_check.lin_lambda`), because a
  closure may run 0..n times and its call count is untracked. M4.5 borrowing did **not** lift this
  and extends the same rejection to borrowed values: whether a captured borrow is sound depends on
  whether the closure escapes and outlives the consume, and Sprout has no escaping/non-escaping
  distinction. Lift this by tracking linear captures against closure arity/call-count, or by adding
  a non-escaping-closure notion. Known-hard (Linear Haskell shipped it incomplete, and its own
  guide still lists "no support for multiplicity annotations on function arguments").

  **The move-into-a-one-shot-closure slice landed separately as M4.4a — see below.** What is left
  here, with its consumers:
  - **A linear value captured at an UNANNOTATED parameter** (the true 0..n case). Includes the
    combinator-over-a-borrow form, `list_each(xs, \x -> write(conn, x))` — the borrow half of
    which needs an escape/lifetime notion, not just a call-count bound.
  - **Linear lambda *parameters***, `\c -> close(c)`. Orthogonal to `once`: that bounds how often a
    closure runs, not what may be handed to it on each run. Needs the lambda's own parameter types
    to carry ownership.
  - **A linear `Scope`** — a `Scope` only ever arrives as a lambda parameter, so it is the linear-
    lambda-parameter case on top of the multiple-use case (`docs/linear-borrowing-v0.md` §2).
- [x] **Pattern-bound linear vars (match var-pattern alias + viral field) — DONE** (2026-08-06,
  post-review soundness fix). `linear_check.pattern_linear_binders` recovers each pattern-bound
  variable's type (structurally matching the pattern against the value type) and tracks the linear
  ones exactly-once per arm; covers the `let..in` alias, `match | g ->`, and the linear-field case.
- [x] **`<-` do-bind of a linear payload — DONE** (2026-08-06, hardened 2026-08-07).
  `linear_check.lin_do_bind` tracks linear do-bind variables. **2026-08-07 soundness fix
  (`do_bind_type`):** the original always took the RHS's last type-argument as the binder type
  (`payload_type`) — right for a *monadic* bind (`x <- (m: Maybe File)` binds `x: File`) but **wrong
  for an effect bind** (`h <- (io: Task a !{IO})` binds the FULL `Task a`, not `a`), so a linear value
  bound with `<-` in an `!{IO}` do-block silently escaped consume-once (found while landing linear
  `Task`). Now: use the full type when it is itself linear, else fall back to `payload_type`.
  Regression: `tests/conformance/type_error/linear_task_double_await.spr`. *Remaining over-strict edge
  (unchanged):* an effect bind of a non-linear container of a linear (`x <- getBox()`, `Box File`
  non-linear) is still conservatively rejected via the payload fallback.
- [x] **Linear `Task a` — DONE** (2026-08-07). `stdlib.task.Task` is a `type linear`; consumed by
  `task_await` or the new `detach`. `with_timeout` internals refactored to inspect the handle once
  (no borrow). Design + prior art: `docs/linear-task-v0.md`. Parametric-`type linear` coverage added
  (`test_parser.spr`, `test_linear_type_decl.spr`) — closes the gap the spike exposed.
- [x] **Discarded linear expression result escapes leak detection — DONE** (2026-08-08). The
  consumed-set analysis tracks *binders* (params, do-`let`, `<-`, pattern vars): every rule asks
  whether the obligation attached to a NAME was discharged, so a linear value produced by a bare
  do-step and never bound had no obligation attached and no rule could find it unfulfilled —
  `do { task_fork(s, w); 7 }` typechecked clean and dropped the handle. `linear_check.lin_do_seq`
  now rejects a **non-final** do-step, and a wildcard bind `_ <- e`, whose value is linear,
  *contains* a linear (`Maybe File`, a tuple), or has a bare type-variable type; the final step is
  exempt, being the block's result, whose obligation passes to the caller (`rest == []` is exactly
  that test). Containment is load-bearing rather than thorough-for-its-own-sake: in a
  `Maybe`/`Result` block every step has type `Maybe X`/`Result E X`, so a head-only test could
  never fire in a short-circuiting block at all — the same reason `do_bind_type`/`payload_type`
  unwrap. A bare linear reference in statement position gets its own diagnostic, because it *is*
  referenced exactly once and telling the author to bind a result they already hold describes a
  rule they have not broken.

  **Post-review (2026-08-08).** A high-effort review found the first cut covered only a head-linear
  type in an effectful block: `_ <- task_fork(…)` — the exact shape the new diagnostic's own advice
  produces — a discard in any `Maybe`/`Result` block, a linear inside a container, and a
  type-variable-typed step all still leaked, while the spec bullet and this entry claimed the class
  was sealed. All fixed above; the docs now state the limits instead of the aspiration.

  Fixtures: `linear_discarded_do_step` (hermetic), `linear_discarded_fork` (the real `task_fork`
  shape — `linear_task_leak` already covered the bound-and-dropped case, this is the never-bound
  one), `linear_discarded_wildcard_bind`, `linear_discarded_result_block`,
  `linear_discarded_container`, `linear_discarded_tyvar_step`, `linear_discarded_bare_ref`;
  positives `test_linear_task.via_trailing`, `test_linear_binders.via_trailing_maybe` and
  `wildcard_non_linear`. Normative text: `docs/spec-v0.md` §5.8, fourth enforcement bullet.

  **Known limits, stated in the spec rather than left implied.** A type variable NESTED in a
  container (`Maybe a`) is not checked — that needs the linearity bound below, and checking it
  would reject most polymorphic `Maybe`/`Result` do-blocks. The bare-tyvar arm is conservative in
  the other direction: it refuses a discard in a polymorphic function that no caller ever
  instantiates at a linear type. Both dissolve once a type parameter can be declared non-linear.

- [ ] `P2` **Decide whether a wildcard pattern over a linear value is a consume or a leak.** A
  *semantics* question the discarded-do-step fix surfaced but deliberately did not answer, because
  answering it either way changes the language rather than fixing a bug. Three shapes, all
  accepted today:
  1. `fn drop_param(f: File) -> Int = match f with | _ -> 0` — a linear **parameter** dropped by a
     wildcard arm.
  2. `match mk(n) with | _ -> 0` — the same over an unbound linear **scrutinee**.
  3. `match w with | Wrap _ -> 0` where `type linear Wrap = Wrap File` — a destructure that drops a
     linear **field**. `linear_viral_field` covers only the double-use direction of viral fields;
     this is the zero-use direction.

  **Position A (what ships today).** "Use" is syntactic, per `docs/spec-v0.md` §5.8: *"'Use' means
  any reference to the binding"*. The scrutinee IS referenced once in 1 and 2, so both satisfy the
  rule; 3 never names the field at all, so nothing is tracked. This is the inherent "a consume need
  not do anything useful" limit — `match f with | File n -> 0` discards the extracted handle and is
  equally uncatchable, and Rust has the same property minus `Drop`, which Sprout lacks.

  **Position B.** A consume should mean the value is destructured *or* passed on, making all three
  leaks. Stronger guarantee; costs a rule that says which patterns count as a real consume, and 3
  is the least debatable of the three (the enclosing `Wrap` being consumed says nothing about the
  `File` inside it).

  The cost of A is that `type linear Wrap = Wrap TcpConnection` + `Wrap _` silently leaks an fd and
  looks deliberate. Needs a call before any code; prior art to survey first (Rust `let _ =` vs
  `let _x =` binding semantics, Austral's linear-field rules).
- [x] **Imported linear-type annotation resolution wart — DONE** (2026-08-07, with M4.5 borrowing).
  Annotating a user param with an imported linear type's bare name failed with
  `Type mismatch: stdlib.task.Task vs Task`. **The old diagnosis here was wrong on both counts:** it
  is *not* a general imported-ADT problem (a non-linear imported ADT annotation always worked) and it
  was linearity-specific. Root cause: `bundler.process_line` had a prefix branch for
  `"export type alias "` but none for `"export type linear "`, so `export type linear Foo` fell into
  the plain `"export type "` branch and `read_ident_at(src, i + 12)` read the contextual marker word
  **`linear`** as the type's name. Every module declaring a linear type therefore exported a phantom
  type called `linear` and never exported the real one, so `is_type_exported` was false and no
  annotation naming it could be qualified to its canonical form. This was a hard blocker for
  borrowing, not a wart: a modifier requires an annotation, so `fn f(c: borrowing TcpConnection)` was
  unwritable outside the defining module. Regression:
  `tests/stdlib/test_linear_cross_module.spr` `consume_annotated`.
- [x] **Enforcement at top-level `let` and instance-method bodies — DONE** (2026-08-06). Wired via
  `letdecl_linear_gate` (LetDecl) and `fn_linear_gate` in `check_instance_method`.
- [ ] `P3` **Containment virality.** Linearity is per-declaration: a record that merely *contains*
  a linear field is not itself linear (contrast Austral, which computes linearity by containment).
  Decide whether to adopt virality; if so, compute a type's linearity from its fields' universes
  rather than only its own `@linear:` marker.
- [ ] `P3` **Cross-module linear-reject conformance coverage.** The `type_error` harness invokes
  `--phase check` without `--package-root`, so a cross-module *misuse* of an imported `type linear`
  cannot be expressed as a conformance fixture. Cross-module enforcement is verified manually and
  by the positive `tests/stdlib/test_linear_cross_module.spr`; add a package-root-aware reject
  harness (or extend `_test-reject` with an optional `--package-root`) to automate the negative.
- [x] **Borrowing (read-without-consume) — DONE as M4.5** (2026-08-07). Swift-style
  `borrowing`/`consuming` parameter modifiers (design note Option D) plus the field-read borrow.
  Design + the five implementation deltas: `docs/linear-borrowing-v0.md` (§16 is authoritative where
  it conflicts with the pre-approval sections); normative text in `docs/spec-v0.md` §5.8.
  `stdlib.net.TcpConnection`/`TcpListener` are now `type linear`: reads/writes borrow, `close`
  consumes, and omitting `close` is a compile error. Migrating the five `tests/task_io_smoke` users
  found that four of them were **leaking their connections outright**.

  Beyond the note's plan, this required (a) a **borrowed set alongside the consumed set plus
  sequential ordering** — M4.2's order-insensitive disjointness check could not see
  `close(conn); write(conn, …)`, since the borrow records no consume; and (b) treating a
  destructured **borrowed value's linear fields as borrowed**, closing a double-consume hole the
  note did not consider. `stdlib.net.tcp_connect` was exported to complete the raw-handle escape
  hatch for shapes borrowing cannot express.

  **Post-merge review (2026-08-07):** a high-effort adversarial review found **ten** defects in
  what landed, six of them soundness holes (double consume or silent leak). All fixed in the
  follow-up, each with a regression fixture; full write-up in `docs/linear-borrowing-v0.md` §17.
  The dominant cause was that the mode lives in a name-keyed side table rather than in the function
  type, so it is lost at any callee that is not a literal top-level name — a `borrowing` function
  used as a value, and a `borrowing` instance-method parameter, both silently read as consuming and
  discharged the caller's obligation. Both are now rejected; see the P2 below for the real fix.
  Also: `LinScope` appended instead of shadowing; the field-access half of "borrowed contents" was
  never closed (the fixture that motivated the M4.5 fix used `match`, so the field form went
  untested); `Maybe`/`Result` do-blocks short-circuit, so a trailing consume does not run on the
  error path; and `mask_is_borrow` passed an end index where `str_slice` takes a length, so every
  MIDDLE borrowing position degraded to consuming — undetected because every borrowing parameter in
  `stdlib/net.sprout` sits at index 0.

  **Follow-up:** the mode-in-the-type work landed as M4.6 (below), which removed the side table and
  with it both blanket rejections. Still open: the owned-record case (P3), arrow-type modifier
  syntax and the linearity bound (both below), and everything gated on M4.4.

- [ ] `P3` **Prelude has no `head` / safe list-destructure.** There is no
  `List a -> Maybe a` in `stdlib/prelude.sprout` at all, so every "look at the first element
  without committing to a non-empty list" site open-codes `match xs with | [] -> Nothing |
  [x | _] -> Just(x)`. Spotted while reviewing `linear_check.conditional_consume`, currently the
  only site in the compiler with that exact shape — which is why it was not worth adding inside a
  soundness-fix PR (the prelude is bundled into the compiler, so any change forces a full reseed).
  Add `head : List a -> Maybe a`, and consider `tail`/`uncons` alongside it; then simplify the
  call site above. Check the wider tree for open-coded instances before settling the name.

- [x] **Put the parameter mode in the function TYPE — DONE as M4.6** (2026-08-08).
  `types.TFunc` carries a fourth field, `types.Ownership` (`OwnConsume` | `OwnBorrow`), beside the
  effect row; the `@parammode:<name>` sentinel is deleted, not supplemented. `linear_check` reads
  each argument position off the callee's type spine, so a lambda, a `let`-bound function, a
  function-typed parameter and a typeclass method are all classified — the side table saw none of
  them and silently read every argument as consuming. Unification compares the tags and rejects a
  mismatch **invariantly** (both directions are unsound: double consume one way, leak the other),
  per Swift SE-0377's "the convention must match exactly" for a noncopyable parameter. That is the
  property worth having: every type flow already goes through `unify`, so coverage is structural
  rather than an enumerated list of sites — and under-covering such a list is what produced M4.5's
  ten defects. Lifted: a borrowing function used as a value, and modifiers on class/instance
  methods (with the instance required to match its class). `IfaceFile` v4 → v5. Golden IR: additions
  only, zero changed lines. Full write-up: `docs/linear-borrowing-v0.md` §18; normative text in
  `docs/spec-v0.md` §5.8. The two things it deliberately did NOT do are filed directly below.

- [x] **One-shot closure parameters — DONE as M4.4a** (2026-08-08). A parameter may be declared
  `once` (`fn task_spawn(scope: Scope, work: once Unit -> Unit !{IO})`), meaning the callee invokes
  it **at most once** and does not store it. That licenses a lambda passed there to **move** linear
  captures into itself: the move is consumed at the call, and must be consumed exactly once inside
  the body. `types.Ownership` gained `OwnOnce` — no new `TFunc` field, so none of M4.6's ~85-site
  fan-out — and `ast.ParamMode` gained `ModeOnce`; the parser change is one arm in
  `param_mode_of_text`, `once` staying contextual under the existing "a type atom must follow"
  guard. `IfaceFile` v5 → v6. Golden IR unchanged (erased, like `borrowing`).
  **The point of it:** `stdlib/http_server.sprout` and `tests/task_io_smoke/concurrent_read.spr`
  now run on the linear `net.TcpConnection`/`TcpListener` throughout, where before they sat on raw
  `Int` handles with no release enforcement — `net.sprout` had shipped a complete linear socket API
  since M4.5 with no consumer at all. `net.read_avail` was added (a Sprout wrapper over the
  existing `tcp_read_avail` extern, not a new builtin) because a header block ends at a delimiter,
  not a byte count. Still rejected, deliberately: captured **borrows** (Rust forbids the same thing
  with `'static` on `thread::spawn`), linear lambda parameters, and captures at unannotated
  parameters. Prior art and the full rule set: `docs/one-shot-closures-v0.md`; normative text in
  `docs/spec-v0.md` §5.8.

- [ ] `P3` **Resources moved into a cancelled task's closure are never released.** M4.4a's
  compile-time guarantee is **at most once** — as are Rust's `FnOnce` and OxCaml's `once`, both
  verified. Rust can afford the weaker bound because a never-called `FnOnce` still runs `Drop`;
  Sprout has no destructors, so leak-freedom for a moved value depends on the callee's runtime
  contract that the closure *does* run. `stdlib/task.sprout:79`'s `with_scope` binds its body with
  `let` rather than `<-` precisely so `__scope_join` is unconditional, which supplies that half —
  except on the cancellation path: `runtime/sprout_scheduler.c:671` `__scope_cancel` walks parked
  tasks and force-drops them (freeing roots), and a spawned task does not start until the current
  task yields, so its closure can run zero times and a moved-in socket is never closed. This is a
  *runtime* leak on the experimental L0.5 cancellation path, not a checker soundness hole, and it
  was equally true of the raw `Int` handles that preceded M4.4a — adopting linear types did not
  introduce it. Fix belongs with cancellation-time resource release (a drop/cleanup hook run on
  force-drop), not with the type system. Spec states the limit: "leak-freedom for moved values
  holds absent scope cancellation" (`docs/spec-v0.md` §5.8).

- [ ] `P3` **A linear `TcpConnection` costs an allocation and a GC root per connection; `wrap` +
  `linear` would not.** Surfaced by M4.4a's golden-IR diff, read rather than regenerated:
  migrating `stdlib/http_server.sprout` from raw `Int` handles to `net.TcpConnection` turned
  `pop_roots(i64 1)` into `pop_roots(i64 2)` in the read loop, because a connection went from an
  unboxed integer to a boxed single-constructor ADT that must be rooted across the recursive call.
  Correct, and the price of the compile-time release guarantee — but not *inherent*:
  `type linear TcpConnection = | TcpConnection Int` is exactly the single-field shape `wrap`
  already unboxes (`examples/wrap_typed_money.sprout`, `test_wrap_codegen.spr` assert no
  `sprout_alloc_obj` / no `sprout_field` for it). If `wrap` and `linear` composed, the linear
  socket API would be free at runtime rather than merely cheap. Scope: find out whether the
  restriction is deliberate or simply unimplemented (`ast.WrapDecl` vs `ast.Linearity`), then
  either lift it or record why it cannot be lifted. Measure before assuming it matters — a server
  doing per-connection syscalls will not notice one allocation, so this is a tidiness / "no hidden
  cost" argument, not a demonstrated bottleneck.

- [ ] `P3` **Export the current `.iface` version as a constant; stop hardcoding it in tests that
  do not care.** Every `IfaceFile` version bump (three so far: v3→v4, v4→v5, v5→v6) costs a sweep
  through test files that pin the literal. That is correct and wanted in
  `tests/stdlib/compiler/test_iface_file_roundtrip.spr`, where the version gate *is* the subject —
  it asserts each retired version is rejected. It is pure friction in
  `test_iface_extraction.spr`, which only needs *a* decodable iface to check that extracted ctor /
  class / instance tables survive a round trip, and whose two failures on the M4.4a bump said
  nothing about extraction. Add `iface_codec.current_iface_version` (or have
  `encode_iface_file` stamp it and drop the field from the constructor) and use it wherever the
  version is incidental. Small, and it removes a recurring false failure that trains the reader to
  bump-and-move-on — which is the habit that would let a real decode regression through.

- [ ] `P3` **Rename `types.Ownership` → `types.ParamConv`.** Once `OwnOnce` lands (M4.4a, one-shot
  closures) the type carries two different things: how the parameter is *taken* (`OwnConsume` /
  `OwnBorrow`) and a bound on how often the callee may *invoke* it (`OwnOnce`). "Ownership" is a
  misnomer for the second. The accurate name is a parameter *convention* — Swift SE-0377's own
  term — so `ParamConv` with `ConvConsume` / `ConvBorrow` / `ConvOnce`. Deferred purely to avoid
  churning every M4.6 site plus the `.iface` wire format weeks after they landed; the widened
  meaning is documented at the declaration in `types.sprout` in the meantime. Pure rename, no
  behaviour change, but it does touch the wire tag names (`encode_ownership`), so it costs an
  `IfaceFile` version bump — worth batching with the next change that needs one rather than
  spending a bump on cosmetics.

- [ ] `P2` **`borrowing` inside arrow-type syntax.** `fn apply(g: (borrowing File) -> Int, f: File)`
  cannot be written: arrow types have no ownership slot, so an annotated arrow means *consuming* and
  passing a borrowing function to one is an ownership mismatch. The gap is **real and not blocked by
  M4.4** — `lin_lambda` rejects lambdas, but a function-typed *parameter* over a linear value
  (`fn apply(g: (File) -> Int, f: File) = g(f)`) typechecks today; an early M4.6 plan draft claimed
  otherwise and was wrong. Deferred for cost, not doubt: it needs a parser change (hence the 2-step
  bootstrap), an ownership field on `ast.TypeExpr`'s arrow with its own fan-out, plus formatter and
  TypeExpr-codec work — and mixing a parser change into a type-system change is what
  `AGENTS.md` rule 2 warns against. Purely additive: it reuses M4.6's `types.Ownership` tag, so
  nothing gets re-migrated. The mismatch diagnostic already tells the author the syntax does not
  exist yet, so this is a known dead end rather than a confusing one.
  Fixture: `tests/conformance/type_error/borrow_fn_as_value`.

- [ ] `P3` **Linearity bound on a type parameter (enabler for `borrowing a`).** A modifier on a
  type-variable parameter stays rejected (`linear_check.bad_modifier_here`), which also blocks the
  receiver-borrowing class shape `class Peekable a { fn peek(r: borrowing a) -> Int }` — so M4.6's
  method lift reaches only a method's *concrete* linear parameters
  (`fn send(p: a, c: borrowing TcpConnection)`). This is **not** a representation limit: ownership
  now sits in the type and survives instantiation. It is a *universe* limit — without a bound on
  `a`, `borrowing Int` is an error while `borrowing a` instantiated at `Int` silently is not, and
  that inconsistency is the tell that this is polymorphism over linear types (an explicit M4.2
  non-goal). Prior art, both verified against the primary sources: **Swift SE-0427** makes generic
  parameters `Copyable` by default — *"generic parameters now conform to `Copyable` by default, so
  the following generic function can only be called with `Copyable` types"* — requiring
  `<T: ~Copyable>` to opt out; **Austral** annotates every type parameter with a universe
  (`Free`/`Linear`/`Type`), so a generic accepting either is written `Pair<L: Type, R: Type>`.
  Scope: pick a spelling, thread the bound through class/fn type parameters, enforce it at
  instantiation, then delete the `type_is_tyvar` rejection.

- [ ] `P3` **Linear-record ergonomics for OWNED records.** M4.5 lifted this only for `borrowing`
  parameters: `p.x + p.y` is legal for `p: borrowing Pos` and remains a reuse for an owned `p`. The
  field-read borrow is keyed on the binding's mode, not on `TGetField` syntax, and deliberately so —
  making every field read a borrow breaks `fn get_x(p: Pos) = p.x` (a passing positive test), because
  a field read *is* an owned record's only consume, and relaxing the leak rule to compensate would
  stop an unclosed socket being a leak, which is the entire point of the feature. The owned case
  therefore needs a real consuming exit: a `RecordPattern` in the language. Blocked on that.

- [ ] `P3` **`&`/`&mut` (shared-XOR-mutable) split.** v0 ships borrow-vs-consume only; a
  read-vs-write refinement is a later increment. `docs/linear-borrowing-v0.md` §2, §13.

- [ ] `P1` **A linear value dropped *inside a container* is not caught — `let..in` binder path**
  (found 2026-08-10 while designing the green-task pool, `docs/green-task-pool-v0.md` §7.4). A bare
  drop is caught; wrapping the value in anything hides it:

  | shape | result |
  |---|---|
  | `let r = Res(1) in 7` | **caught** — "linear value 'r' is never used" |
  | `let xs = [Res(1)] in 7` | silently dropped |
  | `let p = (Res(1), 2) in 7` | silently dropped |
  | `let b = Box(Res(1)) in 7` (user ADT) | silently dropped |
  | `let m = Just(Res(1)) in 7` | silently dropped |

  Distinct from the DONE discarded-do-step work at `:1066`, which added a *containment* test for
  `_ <- e` / non-final do-steps. This is the **pure `let..in` binder**: no obligation attaches to
  `xs` because `List Res` is not itself linear, so no rule ever asks about the `Res(1)` inside.
  Also adjacent to the open Position A/B call at `:1100` (constructor-field discard) — the same
  question of what counts as a real consume. Probes in `docs/green-task-pool-v0.md` §7.4 are
  ready-made fixtures. **Ranked P1 because it is soundness, not ergonomics:** the practical
  consequence is that a resource pool protects its *contents* (each acquired resource must be
  consumed exactly once — verified) but dropping the pool itself with resources inside is silent.

- [ ] `P2` **Raise priority: the over-strict effect-bind fallback now has a concrete consumer.**
  The "*Remaining over-strict edge*" recorded at `:1059` — `x <- e` where `e : Container Linear
  !{IO}` types `x` as the payload, so a non-linear container of a linear is conservatively rejected
  — is what forces `ch <- chan_new(s, cap)` to be written as a threaded parameter instead in the
  worker-pool server (`bench/http_worker_pool/pool_server.sprout`). Verified 2026-08-10 that this,
  **not** linearity propagating from a type argument, is the whole obstacle: `Chan Res` used twice
  as a *parameter* typechecks, `List Res` used twice typechecks, and `borrowing Holder Res` is
  rejected with "only allowed on a parameter of a linear type" — i.e. a user ADT applied to a
  linear argument is not itself linear. Fixing the fallback removes a real shape constraint from
  stdlib code rather than a hypothetical one.


### Linear-typed Sprout-IR (Model C Milestone 5) — DEFERRED (2026-08-07)

M5 (make the IR's heap types linear so GC-rooting is a type-checker theorem) is **deferred**. Full
analysis, options, and the decision rationale: `docs/linear-ir-m5-feasibility-2026-08-07.md`. Short
version: M5 as planned is not executable against the IR that was actually built (no `Heap τ`/`Rooted τ`
type — the IR uses a coarse `IRType` tag + the `ir_rooting.sprout` dataflow pass; the M4 linear checker
is over `TypedExpr`, not `IROp`). A replay of the four historical GC-UAF bugs (`BACKLOG.md:248–251`)
found they are all classification-completeness (A) or sub-op alloc-ordering (B) bugs — never the
"forgot to root a correctly-classified value" (C) class that linearity catches for free. So full M5's
headline benefit is unsupported by bug history, at months-scale cost, and it deepens the non-moving-GC
coupling. The rooting invariant stays enforced by `ir_rooting.sprout` plus the exhaustive no-catch-all
op-classification already in place.

- [ ] `P2` **IR classification-consistency verifier (possible next task; the M5 "Option 2").** A
  greenfield `stdlib/compiler/ir_verify.sprout` pass, wired into `ir_pipeline.compile_program_streaming`
  after `ir_rooting.insert_roots`, run as a CI/debug gate; on failure emits a loud, located
  compiler-internal error. **Not linear types** — it targets the same bug class via classification
  *consistency*, not a consume-once discipline (see the feasibility doc §"how it relates to linear
  types": for GC rooting the safety-critical half is *totality/coverage*, not *no-reuse*; heap SSA
  values are naturally multiply-read, so value-level exactly-once would be wrong). **Teeth (family 1):**
  for every heap-producing op whose kind derives from a type, re-derive the expected kind from an
  *independent* structural source (`type_kind.type_is_non_heap_scalar` for `IRCall` return kinds via
  the callee signature; `field_kinds` for `IRGetField`/`IRLoadEnvSlot`/`IRGetTupleField`) and assert
  the two agree — `IRTUnknown` treated as "either acceptable," never a mismatch (no false positives).
  This catches bug 248's exact shape (`IRCall` with absent/wrong heap kind → mismatch → build error)
  without touching the 447 KB translator. **Family 2 (deferred within the task):** re-verify the
  post-rooting IR directly (every heap value live across a trigger sits in an `IRRoot`/`IRUnroot`
  bracket) — but only with an *independent* liveness/trigger derivation, else it's circular; costs
  more for less historical payoff (catches pass bugs, not classification bugs). Ship family 1 first.
  Weeks, not months; additive; a genuine evidence-gathering down-payment on full M5 (Option 1) should
  that later prove justified.

### Native REPL & Analysis Service

- [x] `P0` **FIXED 2026-08-17. BUG: no spelling of an imported extern worked in the REPL.**
  `import stdlib.bits` then `bit_or(3, 5)` gave `Unknown variable: bit_or`, and `bits.bit_or(3, 5)`
  gave `Unknown variable: bits.bit_or` — while `:type bits.bit_or` answered `Int -> Int -> Int`.
  Affected every extern in every non-prelude module (`bytes_length`, `double_to_bits`, `read_file`);
  `stdlib.bits` was simply the first module that is *entirely* externs. **Root cause:** the question
  "what does an importer see, and under what spelling" was decided independently in five places, and
  `module_loader.decl_value_names`/`prefix_pairs` reached the opposite answer from
  `bundler.add_decl_to_symbols` for `ExternFnDecl` — so the env pre-check demanded the qualified
  spelling and the bundler that actually compiles the eval demanded the bare one. **Resolution:** one
  authority, `ast.decl_value_scopes`/`ast.NameScope`, consumed by both front ends; extern provenance
  rides a new `@extern:` marker family emitted by `infer.pre_scan_extern`, which inherits exactly the
  unprefixed/unfiltered propagation a global name needs. Selecting globals *by marker* rather than by
  declaration is what makes transitive visibility work. Gate:
  `tests/stdlib/compiler/test_module_surface_agreement.spr` (14 assertions, RED-verified 9 pass/5
  fail with every failure on the env path and both controls green). Design:
  `docs/module-surface-authority-v0.md`. Note `bits.bit_or` now correctly FAILS in `:type` too — that
  spelling never compiled.
- [x] `P1` **DONE 2026-08-17. One export authority: both raw-text scanners retired.**
  `parser.skip_export` discarded the `export` keyword and `skip_visibility` the `(..)` marker before
  either reached the AST, so `bundler.scan_source_info` and `repl.gather_exported_names` each
  recovered the publish-set by scanning source *text* — and the REPL's copy accepted only lines
  starting with `export `, which an `extern fn` line never is (and `export` on an extern is a
  parsed-and-discarded no-op, so nobody writes one). **No extern was ever a REPL completion
  candidate, from any module including the prelude**: `print`, `panic`, `int_to_string` all missing,
  and `stdlib.bits` offered nothing at all. **Resolution:** `parser.scan_module_surface` — a token
  scan that decides by calling the parser's own predicates (`is_alias_type_decl`,
  `skip_linear_marker`, `skip_visibility`), so the `export type linear ` / `export type ` prefix
  ordering hazard the text scanner documented cannot exist. `bundler.scan_source_info` delegates to
  it, `repl.gather_exported_names` is deleted, `analysis_service_driver.collect_decl_names` now
  derives its extern exclusion from `ast.decl_value_scopes`, and `process_wi_finalize` stopped lexing
  each module twice. REPL completion parses now (~81ms for the prelude vs ~38ms to tokenize; TAB-only,
  measured before deciding). The `export` field on `ast.Decl` was rejected: better long-term model,
  244-site sweep, no property the token scan lacks — and not blocked by this. Tests:
  `test_module_surface_scan.spr` (21, incl. equivalence with the scanner it replaces),
  `test_repl_completion_surface.spr` (14), `test_repl_module_list.spr` (4, RED-verified by deleting a
  module from the list). Gotcha found and pinned: gating constructor completions on `(..)` silently
  drops `Just`/`Nothing`/`Ok`/`Err`, because the prelude declares those types WITHOUT `(..)` and is
  inlined rather than imported. Design: `docs/module-surface-authority-v0.md` §6.
- [ ] `P3` **`repl.stdlib_module_completion_names` is still a literal** (2026-08-17). Deliberate:
  there is no directory-listing primitive in the language, so enumerating at runtime means `sh -c ls`
  per keypress — a subprocess, and one that silently offers nothing on the Windows port. Staleness is
  caught by `tests/stdlib/compiler/test_repl_module_list.spr` instead. Revisit if a `read_dir`
  primitive ever lands (which needs its own approval per "Builtin vs Stdlib").
- [x] `P2` **Retire the env typecheck path onto the bundler. DONE 2026-08-18.** The structural end of
  "two front ends that can disagree". `compile_source_with_cache_roots` — the choke point every
  editor-facing caller funnels through — now bundles instead of building an environment of imported
  schemes, sharing `check_bundled` with `--phase check` so the two cannot diverge by construction.
  New `bundler.bundle_source_with_roots` + `bundler.LoadEnv` (source overlay for unsaved buffers,
  parsed-module memo, prelude-scheme memo). Found four real divergences on the example corpus, all
  reported by a user in RubyMine and invisible to CI because **no gate ran the env path** — it is the
  *default* phase and every justfile invocation passes an explicit one. Closed by
  `scripts/front_end_agreement.sh` (in `just test`), which compares both front ends over the corpus
  and bounds time, since one of the four was a non-terminating check that wedged the whole LSP
  session. Measured: first check 0.25s, subsequent re-checks 0.04s (the path it replaced: 0.08s and
  0.08s); `tests/golden/ir` byte-identical. Detail: `docs/module-surface-authority-v0.md` §7.
- [ ] `P2` **Delete `module_loader.build_import_pairs*` and the orphan `type_driver` /
  `lower_driver`** (2026-08-18). Those two driver modules are unreferenced — nothing imports them,
  no justfile recipe builds them — and they are the only remaining callers of the retired
  scheme-environment path, each carrying its own copy of it. `build_import_pairs_with_roots` is
  marked RETIRED in-file. Deleting all three finishes the retirement; kept out of the landing change
  to keep it reviewable. `load_prelude_pairs` stays either way — the bundler's `check_bundled` uses
  it for the ambient-prelude case.
- [ ] `P2` **The LSP overlays only the entry document** (2026-08-18). `LoadEnv` can carry every open
  dirty buffer, and that is what makes checking an unsaved multi-file edit correct, but
  `check_and_push_diagnostics` overlays just the document being checked. An unsaved edit in a second
  tab is therefore invisible to a check of the first, which reads from disk. Feed `lsp_documents`
  into the overlay to close it.
- [ ] `P2` **Standing guard: every top-level stdlib module loads cleanly through `load_module`**
  (2026-08-17). Asked for by `docs/repl-env-type-vocabulary-v0.md` §9 and blocked at the time because
  four modules were red; §11.1's fix took it to 26/27, so it is landable now. Would have caught all
  eleven modules of that bug the day they broke. Land it together with the one remaining red module
  (`stdlib.repl`, failing inside the unswept `stdlib/compiler/` subtree) or with an explicit
  known-red list so it cannot silently rot.
- [ ] `P2` **Canonical `<module>.<Type>` identity on the env path** (2026-08-15). Today two modules'
  same-named types collapse to one identity inside an importer — already true for selectively
  imported types (they arrive bare) and now also for alias-qualified spellings, since those resolve
  by dropping the alias. One canonical name per type, matching `bundler.qualified_name`, closes it.
  **Scope is bigger than it looks, and this was measured, not guessed:** every `@`-marker family on
  the env path is keyed by SHORT type name — `@linear:<TypeName>` (read via
  `linear_check.head_type_name`), `@inst:<Class>:<head>` (typeclass dispatch, keys built bare via
  `type_from_ast(head_te, dict_empty())`), `@class:`, `@type:`. Qualifying types makes those
  lookups MISS silently. An implementation attempt reached 22/27 stdlib modules with new
  interaction classes still surfacing (it broke `@linear:`, and would have broken instance dispatch
  for imported types — which a module-load probe does not exercise), versus 26/27 for the
  alias-stripping fix that landed. Doing this properly means moving every marker family to
  canonical keys, or stripping to short names at every marker lookup — a change to dispatch and
  linearity that needs its own design doc and PR. Analysis + measurements:
  `docs/repl-env-type-vocabulary-v0.md` §11.1a; invariant recorded in `docs/compiler-internals.md`.
- [x] `P0` **FIXED 2026-08-15. BUG: an imported type has two identities in the REPL — `T` vs
  `alias.T`.**
  **Resolution.** `prefix_pairs` qualified an aliased import's binding KEYS but left the type
  constructors inside those schemes short, so `bytes.to_string :: Bytes -> Result Utf8Error String`
  could never unify with an annotation `Result bytes.Utf8Error String` (which `lookup_type_var`
  keeps verbatim per T7). Fixed by resolving a KNOWN alias prefix away: `import M as a` records a
  `@qualalias:a` env marker, `infer.import_aliases_from_env` lifts it into `alias_env` — the base of
  every `local_vars` dict (`build_type_var_dict`), so it reaches every annotation position with no
  new parameter threaded through inference — and `lookup_type_var` resolves `a.T` to the short `T`.
  A prefix that is not an import alias (`main.Foo`) is still returned verbatim, so T7 holds.
  Also completed a fourth under-specified stdlib import list (`http_server` ← `HttpUnsupportedStatus`).
  **26 of 27** top-level stdlib modules now load; `import http_server` works in the REPL, which was
  the original report. The last one, `stdlib.repl`, fails in the unswept `stdlib/compiler/` subtree
  (`parser.submission_starts_decl` IS exported — separate defect). Tests:
  `tests/stdlib/compiler/test_repl_type_identity.spr` (4 assertions, RED-verified 2 fail/2 pass —
  the two passing pinned the regression the cheaper alias-qualifying design would have caused).
  Canonical naming was implemented and rejected on measurement; see the `P2` above.
  <details><summary>Original report</summary>
  The last thing standing between the REPL and a working `import http_server`; it owns the original
  bug report. Four modules fail to load through `module_loader` with a type-identity mismatch —
  `repl` (`StatefulSession` vs `compiler.StatefulSession`), `http_middleware` (`Logger` vs
  `log.Logger`), `scram` (`CryptoError` vs `crypto.CryptoError`), `http_server` (`Utf8Error` vs
  `bytes.Utf8Error`). One imported type reaches two positions under two names — bare in one,
  alias-qualified in the other — and unification treats them as distinct constructors. The bundling
  path never sees it because inlining gives every type exactly one name. Uncovered by the
  type-vocabulary fix below, which removed the earlier wall these modules hit first. Regression test
  should be Kuba's original repro: `import stdlib.http_server` then `http_server.default_config()`.
  A standing guard — assert every stdlib module loads cleanly through `load_module` (probe in
  `docs/repl-env-type-vocabulary-v0.md` Appendix A) — should land with the fix; it is red today.
  Analysis: `docs/repl-env-type-vocabulary-v0.md` §11.1.
  </details>
- [ ] `P2` **Decide whether `import M (T)` brings `T`'s constructors into scope** (2026-08-14).
  `select_named_pairs` matches names exactly, so a selective import of a type does not import its
  constructors; the bundler, by inlining, behaves as if it does. **Three** stdlib modules were
  relying on the permissive behaviour and failed on the env path (`net` applying `Utf8DecodeError`,
  `http_server` applying `HttpUnsupportedStatus`, `template`
  matching `JsonFloat`) — both import lists have since been completed, so this is a semantics
  ruling, not a live break. Options: require explicit constructor listing (status quo on the env
  path), make `T` imply `T`'s constructors, or add an explicit `T(..)` form (Haskell spells the
  permissive case that way precisely because `T` alone does not imply it). Ruling belongs in
  `docs/spec-v0.md` §visibility/exports; the bundler and the env path disagree until then.
  Analysis: `docs/repl-env-type-vocabulary-v0.md` §11.2 and §4.4.
- [ ] `P2` **Fix B — `load_module` silently swallows a genuine `CheckErr`** (2026-08-14).
  `module_loader.sprout:366` turns a module that fails to check into an empty pair list, so a
  broken `import` reports `ok` and every name from it reads as `Unknown variable` one command
  later. That swallow is what turned a one-line diagnostic into a multi-hour investigation. Must
  distinguish *intentionally skipped* (`module_name_to_path` → `Nothing`; stays silent, the
  builtin-env path depends on it) from *found on disk but failed to check* (must surface). Deferred
  from the fix below because threading a `Result` through `load_module` reaches 12 call sites
  across 5 driver modules, which is an error-plumbing refactor and Collaboration Rule 2 says not to
  land one on a semantics change. Trap: `load_module` caches `Nil` *before* loading to break import
  cycles, and the `ModuleCache` is shared across every session op — a naive fix reports the error
  once, then serves the cached `Nil` forever. Analysis: `docs/repl-env-type-vocabulary-v0.md` §4.2.
- [x] `P1` **FIXED 2026-08-14. BUG: 11 of 27 top-level stdlib modules were silently invisible in the
  REPL; a session-declared type could not mention a prelude type.**
  **Resolution.** A `@type:<TypeName>` env marker family (`mark_declared_types`), read back by
  `type_names_from_env` — a line-for-line mirror of the `class_names_from_env` sibling one line
  below the broken seed — so the strict type-name validation pass sees imported type vocabulary on
  the path where modules arrive as schemes rather than decls. Markers ride `is_marker_key`, so they
  survive both aliased and selective imports; a prelude-only seed would have missed the latter.
  Fed from the same `collect_declared_type_names` walk the pass itself uses, so exported and
  validated vocabulary cannot drift. Also completed two under-specified stdlib import lists
  (`net` ← `Utf8DecodeError`, `template` ← `JsonFloat`) that the bundler had been forgiving.
  Restored 7 of the 11: `args`, `compiler`, `http_client`, `linalg`, `log`, `net`, `template`; no
  previously-loading module regressed. The remaining 4 fail for the unrelated type-identity defect
  filed as `P0` above. Tests: `tests/stdlib/compiler/test_repl_type_vocabulary.spr` (4 assertions,
  RED-verified before implementation, driving `compile_source_with_cache` — the file `--phase check`
  path does not reproduce any of this). Design doc: `docs/repl-env-type-vocabulary-v0.md`.
  <details><summary>Original report and diagnosis</summary>
  `import http_server` reports `ok`, then
  `http_server.default_config()` fails with `Unknown variable`; `type Box = Box (Vec Int)` is
  rejected outright with ``type-validation: unknown type name `Vec` ``. Root cause: the strict
  type-name validation pass (`infer.sprout:4729`) seeds its vocabulary from the module's own
  `decls` plus a hardcoded primitive list, and ignores `env`. The file path bundles the prelude
  inline so `type Dict v` is in `decls`; the REPL/analysis-service path supplies the prelude as env
  *schemes*, so a decl-scan finds nothing and every module whose TypeDecl/RecordDecl/AliasDecl
  mentions `Result`/`Dict`/`Vec`/`MutVec` fails to check. `module_loader.sprout:366` then converts
  that `CheckErr` into `Nil` silently, so the import looks like it worked. Affected: `args`,
  `compiler`, `http_client`, `http_server`, `linalg`, `log`, `net`, `repl`, `scram`, `template`,
  plus `http_middleware` by cascade; the `stdlib/compiler/` and `stdlib/math/` submodule trees are
  unswept and likely add more. Fn signature positions are not covered by the pass, which is why
  only decl positions trigger it. Fix (needs approval): a `@type:` marker family mirroring
  `@linear:`/`@class:`, read back by a `type_names_from_env` mirroring `class_names_from_env`
  (`infer.sprout:4918`) — markers are already passed through unprefixed by `prefix_pairs` and
  retained by `select_pairs`, so this covers prelude *and* cross-module selective imports; plus
  stop `load_module` swallowing a genuine `CheckErr` (distinguish "intentionally skipped" from
  "found but failed to check", or the builtin-env path breaks). Second instance of the same
  env-schemes-vs-decls divergence (the first was the REPL rejecting `where ToString a`); the
  structural fix is converging `module_loader` with `iface_codec`, deferred. Full write-up,
  prior-art survey, test plan and reproduction probe: `docs/repl-env-type-vocabulary-v0.md`.
  </details>
- [ ] `P1` Implement `complete_in_state` in `analysis_service_driver.sprout` (2026-05-18, updated 2026-07-17): `eval_expr_in_source` (compile-and-run) and `instances_in_source` are now implemented. `instances_in_source` (landed 2026-07-17) bundles the session source (prelude + transitive imports inlined), resolves the query in session naming context via a probe signature, and unification-matches instance heads (reusing `infer.type_from_ast` + `unifier.unify_types`), with a base-constructor fallback so `:i Maybe a` also surfaces `Functor Maybe`; see `stdlib/compiler/analysis_service_driver.sprout` (`resolve_instances` / `instance_match_names_for_type`) and `tests/stdlib/compiler/test_instances_in_source.spr`. Remaining stub: `complete_in_state` (tab completion, returns "not yet implemented"). Approach: reuse `type_of_in_source` machinery; filter by prefix from a gathered list of visible names from imports + declared names.
- [ ] `P2` Fix analysis service env isolation: `SPROUT_GC_THRESHOLD` and `SPROUT_GC_ADAPT_RATIO` must not propagate to the `analysis_service_bin` subprocess (2026-05-23): the GC stress test (`test_native_repl_diagnostics_in_source_survives_forced_gc`) sets `GC_THRESHOLD=1` on the program binary env; the program spawns the analysis service with the same env, causing the native binary to GC on every allocation (~15 min to run). Python service ignored Sprout GC settings (Python runtime). Fix: strip `SPROUT_GC_*` vars from the env before launching the service, or use a separate wrapper script. Test is skipped until fixed.
- [ ] `P2` Fix `symbol_locations_in_source` in `analysis_service_driver.sprout` to include constructor locations (2026-05-23): `collect_decl_locations` emits an entry for `TypeDecl name` but not for its constructors (e.g. `type Fruit = | Banana` produces 1 location for `Fruit`, missing `Banana`). `collect_decl_names` already walks ctors via `ctor_names` so the fix is to add parallel ctor emission in `collect_decl_locations`. `test_native_analysis_symbol_locations_in_source_builtin_runs_via_analysis_service` is skipped until fixed.
- [ ] `P1` Fix `sprout_tag: null pointer` crash in native REPL block-mode tests (2026-05-18): 3 native REPL tests crash with exit code 250 and `[sprout] sprout_tag: null pointer` on stderr. Failing tests: `test_repl_native_launcher_block_mode_runs_mixed_submissions_sequentially`, `test_repl_native_launcher_block_mode_supports_multiline_class_declaration`, `test_repl_native_launcher_block_mode_supports_multiline_function_declaration`. Root cause not yet identified — the crash is in the native REPL binary itself (not the analysis service). Likely a null GC heap allocation in the block-mode parser path for multi-line inputs.
- [ ] `P3` REPL display: zero-param `fn` declarations show `<value: Vec a>` instead of `<fn: Vec a>` (2026-05-26): `fn vec_empty() -> Vec a` has scheme type `forall a. Vec a` (no `TFunc` wrapper — zero-param fns are typed as values, not functions). `is_function_scheme` returns false so `eval_with_scheme` falls through to the `is_polymorphic_scheme` branch and displays `<value: Vec a>`. Users expect `<fn: Vec a>` because they defined it with `fn`. Options: (a) detect at the declaration level that `vec_empty` was defined with `fn` and add a flag to its scheme; (b) use `<fn:` for ALL unprintable polymorphic values (function-typed or not); (c) distinguish with `<thunk: Vec a>`. Deferred — current display is technically correct from the type system's perspective; no correctness issue.
- [ ] `P2` Implement in-Sprout tree-walking interpreter for REPL eval (option 2, long-term eval approach): replace the compile-and-run approach for `eval_expr_in_source` with an in-process tree-walking interpreter implemented in Sprout. The self-hosted compiler already owns parser, typechecker, and lowering — the interpreter is the remaining output mode. Benefits: zero subprocess overhead per eval, in-process session state (no temp files), `instances_in_source` becomes a live type-env query rather than an AST walk, completion and diagnostics share the same live state. Mirrors GHCi's architecture — same typechecker for REPL and compiled code, only the execution backend differs. Implementation target: a `stdlib/compiler/eval.sprout` module that walks `typed_ast.TExpr` nodes and evaluates against an env of `String -> Value`. See `docs/archive/native-repl-roadmap.md` and `docs/repl-self-hosting-v1-draft.md` for design context.
- [ ] `P3` *(optional, far future)* LLVM MCJIT/ORC JIT for REPL eval (option 3): compile REPL expressions to LLVM IR and JIT them in-process via LLVM's JIT APIs (MCJIT or ORCv2). Session module stays resident in the JIT dylib; new expressions linked on-the-fly at native speed with no subprocess fork. Requires embedding LLVM as a library (currently called as an external binary via `--emit-ir` + clang). Not practical until the compiler owns the full LLVM emission pipeline as a library rather than emitting text IR to stdout. Precedent: cling, clang-repl, LLVM `lli --jit`. Revisit after option 2 (in-Sprout interpreter) is working.

### GC & Runtime Performance

- [ ] `P3` Generational GC for native runtime (2026-05-27, **deprioritized 2026-05-28**): split `g_heap_nodes` into `g_young_nodes` (gen=0) and `g_old_nodes` (gen=1); add `generation` field to `ManagedNode`; add remembered set (fixed-size array) populated only in `ref_write` (the sole mutation primitive — Builder creates new objects, closures are never patched) — **WRONG, measured 2026-08-09: `vector_mutset` is also a barrier site and carries essentially all of the mutation traffic (10.6M calls on `digit_recognizer` vs 15.7k total in the compiler); and a single global array is the wrong shape for the declared share-nothing multicore direction. See BACKLOG:283 and `docs/gc-generational-v0.md` §7**; minor GC marks only young objects (old-gen guard in `gc_mark_enqueue`), sweeps young list, promotes survivors to old; major GC is existing full sweep; two orthogonal thresholds: `g_nursery_threshold=512` (minor trigger, young count) and `g_gc_threshold` (major trigger, **old-gen count only**); safety cap at 10× nursery_threshold forces major GC to prevent unbounded young-gen growth; after minor GC, chain to major if old-gen count crosses its threshold. **Expected impact on N-queens: 10–20%.** **Deprioritization (2026-05-28):** macOS `sample` CPU profile (see `docs/archive/nqueens-optim-iteration-2026-05-28.md`) showed `register_managed_ptr` is only ~1% of CPU on N-queens, not the bottleneck the prior write-up assumed. The dominant cost was GC root push/pop (67%), now reduced to ~44% by the type-aware-rooting fix. The draft as written keeps per-object `ManagedNode` metadata and so still touches per-allocation overhead — leaving the actual current bottleneck (function-call cost across LLVM↔runtime boundary in `sprout_gc_push_i64_root`/`pop_roots`) untouched. A **bump-allocated nursery** (no per-object metadata; objects identified by address-range membership) is the higher-leverage variant for allocation-heavy workloads and should supersede this draft when the time comes — but only after the push/pop boundary cost is eliminated (see N-queens P1 backlog entry below). See `docs/archive/generational-gc-v1-draft.md` for the original full design. Branch: `perf/generational-gc`.
- [ ] `P1` N-queens P1 — inline GC root push/pop or enable LTO (2026-05-28, next iteration): after the P0 type-aware rooting fix, GC root push/pop still accounts for ~44% of N-queens CPU. The remaining pushes are for genuine heap pointers (Vec args) so type filtering can't help further; the per-push cost is the **function-call boundary** between LLVM IR and the C runtime — each `call i64 @sprout_gc_push_i64_root(ptr %slot)` costs ~50 cycles of caller-save spill + branch + writes. Two options: **(A) inline as IR** — runtime adds a slim i64-only root stack (`static void* g_i64_root_slots[131072]; static long long g_i64_root_top;`); codegen emits the 3–4 IR instructions inline (load top, GEP, store slot, store new top) instead of a runtime call. SCAN/PTR roots keep the current `RootNode` machinery. **(B) enable LTO** — pass `-flto` to clang on both `runtime/sprout_runtime.c` and the emitted `.ll`; LLVM may inline the helper across the boundary. Option B is the cheaper test (one-line change in `_test-stdlib` and `compile-native` recipes); option A is the canonical fix. Verify B first; if it lands close to Haskell parity (N=12 ~108 ms), ship. Otherwise do A. Expected combined impact with P0 already done: 2–3× additional speedup, landing in the Haskell-UArray neighbourhood. See `docs/archive/nqueens-optim-iteration-2026-05-28.md`.
- [ ] `P2` N-queens P2 — skip re-pushing already-rooted function parameters (2026-05-28): the codegen currently re-pushes function arguments at every call site even when the argument is a `TVar` resolving to a parameter that's already a GC root in the caller's frame. The recursive `queens(n, row, col+1, cols, pos_diag, neg_diag)` re-roots `cols`, `pos_diag`, `neg_diag` even though the current frame already has them rooted. Pure codegen fix: in `emit_args_with_roots`, check if the argument is a `TVar` resolving to a parameter and skip pushing if so. Expected savings: 20–40% on top of P0/P1. Estimated work: a few hours. Multiplicative with P1.
- [ ] `P2` N-queens P3 — True/False/Nil singletons (2026-05-28): mirror the existing `Nothing` singleton in `sprout_make0` (`runtime/sprout_runtime.c:3156`) for `True`, `False`, and `Nil` constructors. Each `vec_set(col, true, cols)` and `false` literal in `is_free` currently allocates a fresh ADT object; with singletons each becomes a constant pointer. Eliminates ~16M of the 33M `sprout_obj` allocations per N=12 run. Expected savings: 10–15%; allocation count drops proportionally with rooting count. Small runtime change.
- [ ] `P2` N-queens P4 — bump-allocated nursery with no per-object metadata (2026-05-28): the canonical generational GC. Distinct from the older "Generational GC" P3 entry above (which splits `g_heap_nodes` but keeps per-object `ManagedNode` and therefore can't reduce per-allocation cost). A bump nursery has objects identified by address-range membership; allocation is `arena_top += size` (~5 cycles vs ~50 for malloc + register_managed_ptr). On minor GC, surviving objects are copied to the old gen and gain full `ManagedNode` metadata. Only worth pursuing after the N-queens P1 inlining lands (push/pop dominates today; bump allocator helps the malloc/free family which is currently ~10% of CPU). See discussion in `docs/archive/nqueens-optim-iteration-2026-05-28.md` "P4 — Generational GC with bump-allocated nursery". Supersedes the older draft when implemented.
- [ ] `P3` N-queens P5 — HAMT persistent vector for `vec_set` (2026-05-28): O(n) → O(log n) for `vec_set`. **Deferred** — at N≤14 vectors are 12–27 elements (single HAMT leaf), so path-copying is the same work as the current O(n) copy. `vector_set` is only 1.1% of CPU; not worth the complexity until larger-N benchmarks justify it.
- [ ] `P2` **GC is now ~59% of self-hosted compile time — it is the next bottleneck, and tuning the existing knobs is not the answer** — *partly superseded: tuning the **right** knob was worth −14% to −19% (see the CORRECTION and DONE sub-bullets below); the claim holds only for the absolute `SPROUT_GC_THRESHOLD`, and structural work is still needed beyond that* (measured 2026-08-08, after the quadratic-`strlen` fix under *CI / Build Performance*). With the byte-offset builtins fixed, `sample` on a compiler-test `--emit-ir` attributes **4399 of ~7500 samples (59%) to `sprout_gc_collect_with_reason`**, and the next entry down is `_platform_strcmp` at 10% (from `map_get_unboxed`'s BST string keys — inherent to a string-keyed compiler, not a defect). So the profile has genuinely moved from string scanning to collection. **What was already ruled out:** raising the threshold buys little for what it costs — `SPROUT_GC_THRESHOLD=2000000` takes a compiler-test emit from 15.5s → 12.9s (−17%) while peak RSS goes 189 MB → 284 MB (+50%), a poor trade against the deliberately live-set-proportional policy at `sprout_runtime.c` ~L1819 (`threshold = live × adapt_factor`, floor 4096), which exists precisely because the old only-ever-grow policy drove multi-GB RSS. `SPROUT_GC_THRESHOLD=200000` is a wash (15.8s). So this needs a structural change, not a knob: the byte-aware trigger below, the bump nursery (N-queens P4), or reducing allocation in the emit path itself. Re-profile before choosing — the 59% figure is post-strlen-fix and supersedes any GC share measured before 2026-08-08.
  - **Re-profiled 2026-08-09 with per-phase timers; the 59% is confirmed and now broken down.** Emitting IR for `stdlib/compiler/ast_to_ir.sprout` (452 KB → 4.17 MB): **mark (roots + drain) 42% of collector time, sweep pass 1 32%, sweep pass 3 (freelist rebuild) 27%, pass 2 0.1%**. Workload shape: **32.2M allocations**, 483 collections, **32.4M object re-marks**, steady state 236k live against a 472k threshold (exactly `live × 2`). Live-set composition at steady state: `obj=200599` (85%), `cstr=29506`/1.28 MB (12.5%), `map=5384`, `tuple=583`, `ref=6`, and **zero** closures/vectors/builders.
  - **CORRECTION — "reducing allocation in the emit path (string concatenation)" was the wrong target.** CSTR is 12.5% of live objects and 1.28 MB; **OBJ is 85%**. The allocation this workload is made of is ADT nodes, not strings. Any allocation-reduction effort should start from the AST/IR node volume.
  - **CORRECTION — the threshold experiment above fought the adaptive policy instead of tuning it.** `SPROUT_GC_THRESHOLD` is an absolute count, so it works against `threshold = live × adapt_factor`. Tuning the factor itself (`SPROUT_GC_ADAPT_FACTOR`, already supported) gives a far better curve on the same workload: **F=3 → −19% time / +18% RSS; F=4 → −29% / +38%** (3 reps; RSS is noisy at ±15%). Roughly a 1:1 time-for-RSS trade at 97→115 MB absolute, not the −17%/+50% the absolute knob suggested. Changing the *default* factor is a policy decision affecting every Sprout program's memory profile and wants its own discussion — but it is a live option, not a ruled-out one.
    - **DONE (2026-08-09) — default raised 2.0 → 3.0**, and the "affects every Sprout program" framing above was **wrong**: the threshold is floored at `SPROUT_GC_THRESHOLD` (4096), so the factor is *inert* for any program whose live set is under ~2048 objects. Of the seven ageprof workloads only the compiler (66,955 live/cycle), `digit_recognizer` (4,492) and `astar` (2,809) are affected; nqueens/http×2/math are floor-pinned and provably unchanged. Measured: compiler emit −14% time / +13% RSS (3 reps interleaved, reproducing the −19%/+18% above on a different emit); `digit_recognizer` **flat time, +1.3 MB RSS**; `astar` flat/flat. The predicted byte-blindness amplification (`:1380`) did **not** materialise — `digit_recognizer`'s live set is small ADT nodes, not the large `malloc`'d backing arrays that would trigger it, so that risk stays open for future large-buffer churn rather than being disproven. Mechanism isolated on `test_gc_age_retain_all`: cycles 9→6, `marked_total` 558,057→313,843 (−44%), `freed_total` **identical** at 420,043 — nothing deferred, only batched. F=4 is available but worse than 1:1 (−29%/+38%). New gate `just gc-adapt-check` pins both the default and floor-inertness. Full rationale + tables in `docs/gc-generational-v0.md` §5.3.
    - **BUG FIXED in passing: `SPROUT_GC_ADAPT_RATIO` had not been a ratio for some time.** Its only use is `if (g_gc_adapt_ratio > 0.0)` — an on/off switch; no swept fraction is compared against it since the policy became `live × factor`. The declaration comment, the env-parse comment, and the user-facing table in `docs/development.md` all still documented the old only-ever-grow semantics. All three corrected.
  - **TOOLING TRAP — `just gc-profile` (`-DSPROUT_GC_PROFILE`) over-reports GC by ~2.3×.** Its `SPROUT_PROF_HOT` counters fire per heap-lookup / mark-edge / sweep-visit (66M + 56M + 65M on this workload) and inflate `gc_us` to 4.13s against a true ~1.7s. Size the collector with per-phase timers or an uninstrumented A/B, never with that build's `gc_us`.
  - **MEASUREMENT TRAP — this machine's absolute timings drift ~1.6× between sessions.** The same control binary measured 2.70s and 4.33s user on the same input hours apart. Only interleaved same-session A/B is meaningful; a "before" number captured earlier in a session is not a baseline.
  - **MEASURED (2026-08-09) — the `region_find` binary search is ~14% of compile time; design in `docs/gc-arena-lookup-v0.md`.** `sprout_heap_lookup` (called per traced edge, per scanned root word, and from the mutation hooks) starts with a binary search over `g_regions`. Both it and `region_find` are `static` and fully inlined at `-O2` — **neither symbol exists in `nm` output**, so `sample`/profilers cannot attribute to them and the earlier practice of reading GC cost off a profile is unavailable here. Measured instead by **sensitivity**: a build performing a second redundant search per call, result escaping into a `volatile` so it cannot be folded, ran **2.547s vs 2.232s baseline (+14.1%)**, 6 interleaved reps, every probe rep above every baseline rep. One search ≈ 14% of runtime, so eliminating it saves at most that. Caveat: the probe doubled *all* callers, so this is not decomposed by caller, and the rest of `sprout_heap_lookup` (the `is_large` test, slotmap probe, header read — genuine random accesses) is not included in what an O(1) lookup would remove.
    - **NEGATIVE RESULT — do not re-attempt a one-entry region hint.** Caching the previously-found region index (validated before use, same discipline as `g_open_region_hint`, so realloc-safe) gave **no improvement** (2.12s vs 2.14s, within noise). Combined with the +14.1% above: the search is expensive *and* its locality is poor, because marking drains the worklist in object-graph order, which is unrelated to the address order the heap was allocated in — consecutive lookups land in effectively random regions among ~110. **Any caching scheme fails for this reason; the fix must make a cold lookup cheap.**
    - **LANDED (2026-08-09) — reserved arena, compiler emit −5.3% min / −7.3% median (97% of 64 interleaved pairings).** Falls short of the ~14% bound above exactly as predicted: the bound covered every `region_find` caller, while the arena removes only the search, not the `is_large` test, slotmap probe, or header read after it. Neutral on `digit_recognizer` (−0.2%), `math` (−0.6%), `http_log` (+0.5%), `nqueens` (+0.2%), `retain_none` (−0.8%). **Accepted cost: `astar` +1.3–1.6%** — it holds exactly ONE region for its whole run, so its "binary search" was a single iteration and already O(1); nothing can beat that, and the win scales with `log2(region_count)` while the cost is fixed. New `just gc-arena-check` (26 fast gates now) + `tests/stdlib/test_gc_large_object_arena.spr`. Emitted IR byte-identical. **Three self-inflicted regressions were found and fixed while building it — read `docs/gc-arena-lookup-v0.md` §12 before touching `region_find`:** (a) putting the fast path and the search in one function broke `sprout_heap_lookup`'s inlining and cost more than the algorithm saved (astar +7%, compiler's win cut to 4–6%); (b) the chunk-map reindex was O(arena_chunks)=4096 rather than O(regions), and region churn made that hot (1.6–6.9% regression); (c) the dominant query is "not a heap pointer at all" — every scalar in every root slot — which used to walk the whole search, now rejected inline by a global heap-bounds compare. Also two measurement errors worth not repeating: linking a `.ll` without `-O2` (mutator 10× slower, fabricated a −12.9% "win") and non-interleaved reps (falsely blamed `madvise` for +2.1%).
    - **Direction chosen: reserved contiguous arena** (`mmap(PROT_NONE)` reservation, `mprotect` per 1-MiB chunk) so membership becomes one subtract + one unsigned compare with no memory access, and the region index is a shift. Preferred over a Boehm/Go-style side table specifically because the rooting protocol queries **arbitrary words**, so "not a heap pointer" is the dominant answer — a side table needs a load to say no, a range check does not. Large objects (`slot_bytes > 4096`, arbitrary size, may exceed 1 MiB) stay `malloc`'d in a **separate** sorted overflow table, which also makes the fallback cheaper than today's search because it no longer contains the ~110 normal regions. Degrades to today's behaviour if the reservation fails or the arena is exhausted. **Gate requirement: a counter proving the arena path is actually taken** — per the PR #48 lesson, a silently-falling-back optimisation passes every existing test while doing nothing.
  - **Pass 3 (27% of collector time) is now LANDED — see the entry below.** The remaining structural target is mark + pass 1, i.e. the nursery: 85% of the live heap is long-lived AST/IR re-traced on all 483 collections, which is what the 32.4M re-marks are. **Confirmed 2026-08-09 by direct measurement: 97% of this workload's marking is re-marking (`docs/gc-generational-v0.md` §5). But it is a COMPILER-ONLY finding — the same instrument puts the ceiling at 13% on the real HTTP server, and `SPROUT_GC_DISABLE=1` makes nqueens faster-not-slower, so this 59% does not generalise beyond self-hosted compilation.** Separately, 34% of pass 1's slot-steps are stepping over already-FREE slots — a fragmentation tax that argues the same way.
- [x] `P2` **Sweep no longer walks the heap twice — freelists are built during the classifying pass. DONE (2026-08-09).** The freelist rebuild was a second full traversal of the heap per collection (97M slot-steps to classify, 97M more to rebuild), measured at **27% of collector time and ~15–17% of total self-hosted compile time**. Fixed by staging: pass 1 pushes FREE slots straight onto the per-class lists while saving each class's pre-region head, so a region that pass 2 then releases has exactly its own entries rolled back in O(classes touched). Measured **−15% user time** on `ast_to_ir.sprout` (2.60s → 2.21s, 3 interleaved reps) with **byte-identical emitted IR**. Two quiet-corruption hazards: slots freed in an earlier cycle must be re-listed, and the rollback is load-bearing (skipping it survived **422** collections of a real compile before producing a dangling entry). Verified with `SPROUT_FL_VERIFY=1`, a per-cycle oracle comparing the staged lists against a full post-release heap walk as a sorted `(class, payload)` multiset: **11,564 collections / ~66.7M entries with zero disagreements**, and the oracle itself validated by deliberate breakages. Deliberately **not** job-wide in CI (it triples a compile-heavy run); covered by `just test-stress` (max collection count, small heaps) plus `just test-freelist-verify` in `ci-fast-gates` (default threshold, multi-region heaps that die wholesale — the case that actually exercises release/rollback). See `docs/compiler-internals.md` §Non-moving GC.
  - **Code review (high) found and fixed three real gaps before merge, all in the *verification*, not the change.** (1) The first regression test never made Pass 2 release a region — measured `region_release=0` — so a deleted `fl_region_rollback()` passed it; `tests/stdlib/test_gc_region_release.spr` now drives **14** releases and catches that mutant at cycle 6. (2) The test's Sprout-level assertions were mutation-tested and detect **nothing** — all three broken sweeps still printed "12 passed" — so both files are documented as workloads whose only oracle is `SPROUT_FL_VERIFY`, and a green `just test` must not be read as covering this. (3) The oracle dereferenced staged entries before validating them, so the dangling-entry case it exists to catch could fault with no diagnostic; it now calls `region_find` first and names the bad pointer. **Lesson worth keeping:** the oracle was validated against breakage on the *big compile*, then a smaller workload was shipped as the regression test and the coverage was assumed to transfer. It did not. Verify that a test reaches the state it claims to guard — with a counter, not by reasoning.
  - **Pass 3 was then removed entirely.** Review observed it existed only to undo Pass 2's `kept_normal > 1` force-keep, which the pre-existing `kept_normal == 0 → open_new_region()` fallback already covers. Releasing empty regions unconditionally collapses the rollback predicate and the release predicate into one shared condition, deleting the pass, its `live_count/poison_count` skip guard, and the standing hazard that the guard had to mirror Pass 1's commit decision exactly or double-list a slot. Cost: one 1 MiB `free`+`malloc` on a cycle where every normal region came out empty.
  - **The oracle-under-stress combination cost CI 10 minutes, and I shipped the wrong measurement.** Enabling `SPROUT_FL_VERIFY=1` for every `test-stress` file took that CI step from ~300s to **883s** on both branch runs; Kuba caught it watching the step sit at 15 minutes. Cause: `test_ir_codegen_ctors` is the *serial critical path* of the recipe — 180s under stress alone, **434s** with the oracle — while all 15 other files finish in under 2s either way. The justfile comment claimed "worst file +0.4s" because I had timed the small files and never the one that dominates. Fixed by an `FL_VERIFY_SKIP` list (oracle on by default so new files stay covered; that one file excluded with the measurement in the comment). **Two general leads this exposed:** (a) `test-stress` prints nothing until every file has finished, so a slow file is indistinguishable from a hang — per-file verdicts should stream; (b) `test_ir_codegen_ctors` taking 180s under stress while its 15 peers take under 2s is worth a look on its own — the whole step's latency is one file's heap shape, and the JOBS fan-out cannot help a single serial run.
  - **Follow-on, and a prerequisite for the nursery:** the freelists are still wiped and rebuilt from *all* regions every sweep. A minor collection that marks only young objects but still rebuilds the whole heap's freelist is not proportional to the young set, which defeats the nursery. Making the lists generation-scoped — stop wiping, remove/re-add only the swept regions' entries — is the natural next increment, and the per-region touched-class bookkeeping it needs already exists (`fl_region_commit`/`fl_region_rollback`).
- [ ] `P2` **GC trigger is object-count-blind, not byte-aware** (`runtime/sprout_runtime.c` `sprout_gc_maybe_collect_threshold`, ~line 879): the collector fires when `g_managed_heap_count >= g_gc_threshold` (default 4096 objects, adaptive ×2 up to an optional cap), and `g_managed_heap_count` increments by exactly 1 per managed object regardless of size — a `VectorVal`'s backing `data` array is allocated via plain `malloc`, invisible to the trigger. Consequence (measured on a 20k-append benchmark, `SPROUT_DEBUG_GC=1`): many-small allocs over-collect (the old list-based `Semigroup (Vec a)` round-trip spewed ~3000 cons cells per append → **40,002 collections**, low peak RSS ~12 MB but ~2s pure GC overhead); few-but-large allocs under-collect (the `vector_concat` builtin allocates 1 managed object per append → only **11 collections**, but ~4096 dead 16 KB result vectors pile up between cycles → ~64–95 MB transient peak, ~40× faster but higher peak). Not a leak — reclaimed next cycle — but higher peak RSS can be the direct flip side of an allocation-count speedup, and will resurface with `MutVec`/large-buffer churn. Fix: make the trigger byte-aware (count bytes, not objects) so large payloads count proportionally toward the threshold. Tunable today without touching data structures via `SPROUT_GC_THRESHOLD`. Surfaced 2026-07-12 landing the `vector_concat`/`Semigroup (Vec a)` dispatch fix (§5 above); see also `docs/gc-profile-findings-2026-07-03.md`.
  - **Now amplified by the `adapt_factor` default of 3.0** (`:1371`): the garbage budget between collections is `(factor − 1) × live` *objects*, so a workload retaining large invisible payloads now tolerates 2× as many of them as it did at 2.0. Measured harmless on today's workloads (`digit_recognizer` +1.3 MB — its live set is small ADT nodes, not big buffers), but this raises the cost of the byte-blindness bug for any future large-buffer churn. If a workload shows unexpected peak RSS, try `SPROUT_GC_ADAPT_FACTOR=2` before assuming a leak.

- [x] `P1` **Green-task setup is 75% of HTTP-server CPU; a worker pool is measured at 3.5–5.4× —
  design + prototype in `docs/green-task-pool-v0.md`. `serve_pooled` LANDED 2026-08-10** (Design A,
  Sprout-level, no scheduler change). `serve`/`serve_n` are untouched and the pool is opt-in:
  `serve_pooled` / `serve_pooled_with` / `serve_pooled_n_with`, defaulting to 8 workers on a
  64-deep bounded channel. The §5.2 bounded-concurrency objection was answered by bounding handler
  occupancy FIRST (see the http_server occupancy entry): with a finite worst case per connection,
  N workers can no longer be wedged permanently by N crawling peers, which is what made pooling
  unsafe before. Regression: `tests/task_io_smoke/http_pooled_serve.spr` — two workers serve five
  connections, so reuse is asserted by connections 3–5 rather than by a counter, and a handler
  driven into the 500 fallback must not cost the pool capacity.
  - [ ] `P2` **A panicking handler kills its worker, and Sprout cannot catch it.** With
    spawn-per-connection a panic killed one connection; in a pool it permanently removes capacity,
    and a server that loses all workers stops answering with nothing logged. `handle_connection`
    consumes the connection on every path and returns `Unit`, so ordinary client misbehaviour
    (malformed requests, timeouts, resets) is already contained — this is strictly about a `panic`
    in user handler code. Both honest fixes are language-level: catchable failures, or a supervisor
    that observes worker exit and respawns. Gotcha A3 in `docs/green-task-pool-v0.md` §5.4.
  - Remaining from the original entry: Every accepted connection
  spawns a fire-and-forget task that `malloc`s ~1.5 MiB (1 MiB `SPROUT_TASK_STACK_BYTES` + 512 KiB
  root pool = 16384 × 32-byte `RootNode`) and frees it. `sample` under `wrk -t2 -c40`: **`madvise`
  2779 samples** (`pump_loop → sprout_roots_free → free_medium`) + **`__bzero` 2009**
  (`task_create → makecontext`) of 6,384 main-thread samples, while the handler self-reports
  `dur_us=3..6`. `makecontext` cost is **linear in `ss_size`** (415 ns at 4 KiB → 5,993 ns at
  1 MiB). Shrinking both constants gives **3.9×** (8,344 → 32,951 req/s) but costs recursion depth,
  so it is a knob, not the fix.
  - **Prototype measured (`bench/http_worker_pool/`, two servers with byte-identical handling):
    3.5–5.4× throughput and 25–40× less RSS** — 8,942 → 31,306 req/s, **202 MB → 8 MB**, p50
    1.41 ms → 297 µs, p90 2.88 ms → 440 µs. Written **entirely in Sprout**: N long-lived workers
    pull owned `TcpConnection`s off a `Chan TcpConnection`. No runtime change.
  - **The RSS direction was predicted backwards and the correction is the useful part:** pooling was
    expected to *raise* steady-state RSS (retaining peak). It lowers it ~30×, because
    `makecontext` zeroes the whole 1 MiB stack and therefore makes **every page of every per-request
    stack resident**. The zeroing is the memory cost as much as the CPU cost. A *grow-on-demand*
    pool would still need a trim policy; a fixed worker count bounds memory by construction.
  - **Linearity governs the handoff and is verified sound** — `chan_send` consumes (send-then-use
    and send-twice both rejected), a received `Got conn` must be consumed (drop rejected), and a
    3-value round trip compiles and runs. This is why the Sprout-level design beat the
    runtime-level one originally proposed: the C pool re-created stale-reference and
    use-after-release hazards (doc §6.1 G5/G10) one layer below where the checker can see them.
  - **The prototype's p99 tail is the benchmark client, not the server.** Two candidate causes were
    tested and both ruled out: GC (max pause 6.9 ms, 21 ms total over 6 s) and the 16-deep listen
    backlog (raising it to `SOMAXCONN` changed p99 not at all). It is ephemeral-port exhaustion from
    `Connection: close` — see the keep-alive entry below. Drained and kept inside the port range the
    pooled server measures **p50 = 35 µs / p99 = 243 µs**.
  - **Open decision: bounded concurrency.** N workers means at most N in-flight requests, against
    `stdlib/http_server.sprout:474`'s promise that a slow connection "cannot block others". Cost is
    smaller than feared — **w=2 already reaches full throughput** (w=2/8/64/256 all 23k–32k), since
    the server is accept-bound. Recommend bounded workers + bounded channel (backpressure), landed
    as `serve_pooled` first so nothing existing changes. Doc §5.2.
  - Design B (pool `Task` records inside the scheduler) is analysed with 17 hazards in doc §6 and
    **not** recommended; it is the only option for `task_spawn` workloads not written as a pool.
- [ ] `P2` **`serve` is a client-driven memory exposure** (measured 2026-08-10): ~1.5 MiB of stack
  per concurrent connection, fully resident because `makecontext` zeroes it. 40 `wrk` connections
  hold **130–237 MB**; the shape scales with client-chosen concurrency. Sprout copied Go's
  goroutine-per-connection model while paying **512× Go's per-task cost** (1 MiB vs `stackMin =
  2048`) — and Sprout cannot follow Go's answer of growing small stacks, because growth needs
  `copystack`'s "adjusts all pointers to reference the new location" and Sprout's rooting is
  non-moving by design. Independent of which pooling design lands. `docs/green-task-pool-v0.md` §3.1.
- [x] `P1` **`connect()` was blocking, so one stalled connect froze every green task in the
  process** (found 2026-08-10, fixed 2026-08-10). `tcp_connect` now goes non-blocking *before*
  `connect()`, parks on write-readiness for `EINPROGRESS`/`EINTR`, and reads the outcome from
  `SO_ERROR`. Measured cost of the bug: **~7.5 s of fully frozen scheduler per stalled connect** on
  macOS (minutes on Linux's SYN-retry ladder), during which no timer could fire, so `with_timeout`
  could not bound a connect at all. Regression: `tests/task_io_smoke/connect_park.spr`.
  Two things the fix had to get right, both easy to miss:
  - The in-flight socket is a **bare fd no handle table owns**, so a `with_timeout` force-drop was
    the only thing that could close it → new `scheduler_park_on_unowned_fd` + `Task.park_close_fd`,
    which `force_drop_task` closes. Verified by negative control (disabling the close exhausts a
    64-descriptor cap mid-test and surfaces as `Too many open files`).
  - `getaddrinfo`'s list is **malloc'd, and `force_drop_task` frees only the green stack** — so the
    candidates are now copied onto the stack and the list released *before* any park can happen.
    General rule for any new park site: whatever is held across a park must live on the task stack.
- [x] `P1` **`with_timeout` leaked a timerfd per expiry on Linux** (found + fixed 2026-08-10). When a
  deadline FIRED, `__await_deadline` returned on all four of its post-expiry paths without tearing
  the timer down, and `scheduler_park_on_timer` only tore its own down. Invisible on macOS —
  kqueue's `EVFILT_TIMER` consumes no descriptor — and every pre-existing timeout fixture expires
  only one or two deadlines per process, so nothing caught it. Fixed by making the **pump** the
  single owner of a fired timer's teardown (it is the only party that knows the timer fired);
  teardown must stay exactly-once because the Linux backend `close()`s the fd, and the other two
  sites (trampoline, `force_drop_task`) run only while the timer is still *live*, so they are
  mutually exclusive with the harvest path. Caught by `connect_park.spr`'s descriptor-budget
  assertion (80 expiries under `ulimit -n 64`) — on Linux CI, not locally. Generalizable lesson:
  **a per-iteration descriptor leak is only visible under a descriptor cap plus enough iterations**,
  and the two poller backends differ in whether timers cost an fd at all.
- [x] `P2` **The whole HTTP client was blocking, not just its connect** (found 2026-08-10, fixed
  2026-08-12). Both client paths (plain and TLS) set `SO_RCVTIMEO`/`SO_SNDTIMEO` and then ran
  `connect`/`send`/`recv` on a **blocking** socket, freezing the pump for the entire call: no
  sibling task advanced and no timer fired. Measured: **2001 ms of frozen scheduler for a 2000 ms
  request**, and **7.85 s to complete a nominally 1 s connect** (macOS does not apply `SO_SNDTIMEO`
  to `connect` at all). Now non-blocking end to end, parking at every wait.
  Regressions: `tests/task_io_smoke/http_request_parks.spr` (liveness),
  `http_request_total_deadline.spr` (semantics), `http_request_cancel_drop.spr` (cancellation).
  Four things the fix had to get right:
  - **`timeout_ms` is now a TOTAL request deadline**, not the accidental per-syscall bound
    `SO_RCVTIMEO` provided. Follows Go's `http.Client.Timeout` and reqwest's `timeout`; deliberately
    unlike `tcp_write_all_timeout`, which stays idle on nginx `send_timeout` prior art (server-side
    per-operation, where cutting off a slow-but-reading peer is the failure to avoid). The old
    behaviour let a peer dripping one byte per timeout window hold a request open without bound.
  - **Cancelling a request became possible, and therefore became a leak.** A blocking client never
    parks, so it was never a `force_drop_task` candidate; parking made it one, and that function
    frees the green stack without unwinding the C frame. The fd is handled by parking through the
    *unowned*-fd entry point at every site (the client's socket is never in the handle table, so the
    parked frame is its only reference); the buffers needed a new mechanism,
    `scheduler_set_park_cleanup`, because a response buffer grows across arbitrarily many parks and
    so cannot obey the standing "keep it on the task stack" rule. Both verified by negative control:
    parking via the owned-fd entry point drops the descriptor budget test to 59 of 80, and the
    cleanup hook is observed firing exactly 80 times.
  - **On a non-blocking fd, SecureTransport's `errSSLWouldBlock` retry loops become hot spins.** The
    old `continue` was only safe because the blocking `recv` underneath did the waiting. All three
    loops (handshake, write, read) now park, on the direction the IO callback recorded — SSL itself
    never says which way it blocked. macOS-only code, so **CI does not cover it**; verified locally.
  - **`getaddrinfo` is still blocking** and deliberately out of scope — see the item below.
- [x] `P2` **`getaddrinfo` froze the scheduler for the duration of a DNS lookup — FIXED 2026-08-12
  (code review finding 10).** Everything else on the client path parks; this was the last blocking
  call, so a slow/unreachable resolver stalled every green task. No non-blocking POSIX resolver
  exists (`getaddrinfo_a` is glibc-only, absent on macOS/BSD), so this was a design decision — see
  **`docs/async-dns-v0.md`** for the full writeup and the verified prior-art survey. **Chosen: Option
  A (resolver thread)** over Option B (native in-Sprout DNS over UDP), because A preserves
  system-resolver parity (`/etc/hosts`, mDNS, nsswitch — the parity Go can't close, which is why it
  keeps a cgo fallback), is the smaller change, and matches the dominant single-event-loop precedent
  (libuv/Tokio/curl defaults all offload `getaddrinfo` to threads). `async_resolve` runs `getaddrinfo`
  on a **spawn-per-lookup detached thread** (the first OS thread in the runtime), the green task parks
  on a per-request self-pipe; results published via an acquire/release atomic (the pipe is only the
  wakeup). The thread is a **pure-libc island** — touches only a malloc'd `DnsRequest`, never GC nor
  `g_current_task` — so the GC stays effectively single-threaded. Concurrency capped at 64 with a
  synchronous fallback (never worse than before; cf. Go's 500 cap). Cancellation: force-drop closes
  the pipe read end (`park_close_fd`) + drops the task refcount (`dns_abandon` park_cleanup); the
  detached thread finishes in the background (getaddrinfo is uncancellable, as for libuv/curl), its
  write hits EPIPE, last decref frees — no leak. **Option C** landed alongside: numeric-literal hosts
  skip `getaddrinfo` via `inet_pton` (`dns_try_numeric`). Tests: `dns_resolve_parks.spr` (RED→GREEN
  liveness, 1006 ms freeze measured on the blocking path, via the `SPROUT_DNS_RESOLVE_DELAY_MS` seam)
  and `dns_resolve_cancel_drop.spr` (80 mid-resolve force-drops under `ulimit -n 64`, no fd leak).
  **Follow-up (B):** a thread-free native resolver + UDP surface + in-process TTL cache, if
  cancellable/cacheable resolution is ever wanted; and a shared eventfd/pipe to drop the per-lookup
  2-fd cost. See `docs/async-dns-v0.md` §9.
- [x] `P1` **CI ran one job on `ubuntu-latest`, so macOS was never tested — and the TLS client path
  had NO automated coverage at all** (surfaced 2026-08-12 while making the client non-blocking,
  fixed 2026-08-12). Two holes, one fix: a `macos-latest` CI job running `task-io-smoke` (CI's
  first kqueue coverage) plus a new `just http-tls-gate` (the first coverage of `http_request_tls`
  in any form). The gate is hermetic — `openssl s_server` holding a leaf from a CA it generates and
  discards, with `SPROUT_HTTP_CA_CERT` as the anchor — so it needs no network and no public
  certificate. Free: standard GitHub-hosted runners, `macos-latest` included, are free and
  unlimited on public repositories.
  Three things the gate had to get right, each found by measurement because the error text is
  identical for all of them (`tls certificate verification failed`):
  - **A single self-signed certificate used as both leaf and anchor is REJECTED.** macOS requires a
    TLS server certificate to carry `extendedKeyUsage=serverAuth`, which `openssl req -x509` does
    not add, so the gate builds a real two-level chain.
  - **A CN alone is not enough** — the leaf needs a `subjectAltName`.
  - **The anchor must be DER**; `SecCertificateCreateWithData` rejects PEM.
  The negative control is what gives the gate teeth: the same request must FAIL without the anchor
  (measured `-9807`, `errSSLXCertChainInvalid`). Without it, a trust evaluation that accepted
  everything would pass the success case silently.
  Note `opt` is a hard dependency of `bootstrap-from-seed` (it verifies the committed seed) and
  Xcode ships `clang` WITHOUT `opt`, so the job installs Homebrew LLVM — at exactly the path
  `mise.toml` already prepends.
- [ ] `P3` **No idle/read timeout for the HTTP client, only the total one.** `timeout_ms` is now a
  total deadline, which is right for the common case but cannot express "fail if the peer stalls for
  N seconds" on a long transfer — a caller streaming a large body has to size one budget for the
  whole thing. Both reference APIs ended up offering both knobs (reqwest has `timeout` *and*
  `read_timeout`; Go pairs `Client.Timeout` with `Transport.ResponseHeaderTimeout`). Additive, so it
  can wait for a caller that actually streams.
- [ ] `P3` **`listen(fd, 16)` — a 16-deep accept backlog** (`runtime/sprout_runtime.c:8341`), well
  below every convention (`SOMAXCONN` = 128 on macOS, nginx's 511). **Downgraded from P2 and from
  "sole cause of the p99 tail": measured NOT to matter here.** Raising it to `SOMAXCONN` left p99
  unchanged (74–120 ms before and after, 3 interleaved rounds), so this is hardening, not a fix.
  Recorded because it was a plausible-looking diagnosis that measurement killed. No hermetic
  regression test is available: a too-small backlog manifests as an unbounded park — the kernel drops
  the SYN and no error reaches either side, so there is nothing for a test to assert on. (The
  blocking-`connect` bug above used to be a second blocker; that one is fixed, so a test *can* now
  at least time the park out — it still cannot distinguish "backlog too small" from "peer slow".)
- [ ] `P2` **HTTP keep-alive: without it, no high-throughput measurement here is server-bound.**
  `Connection: close` means one TCP connection per request; this machine has **16,384 ephemeral
  ports**, so a 4 s run at ~32k req/s opens ~131k connections — 8× the range — and the client stalls
  on `TIME_WAIT` recycling. That, not the server, is the worker-pool benchmark's p99 tail: drained
  and kept inside the port range the same server measures **p50 = 35 µs / p99 = 243 µs**, versus
  p50 = 536 µs / p99 = 66 ms on a long run. Keep-alive would make tail latency measurable at all,
  and is a real feature gap besides. Protocol constraint documented in
  `bench/http_worker_pool/bench.sh`.
- [ ] `P3` **The accept loop is the next server bottleneck.** Worker count is irrelevant from w=2 to
  w=256 (doc §5.3), so once task creation leaves the per-request path the single accept task is the
  limit. Any further HTTP-server work should start there, not at the handler.
- [ ] `P3` **`chan_select` allocates per call** — `malloc(n * sizeof(Chan*))` on every call (freed on
  every exit path) plus `malloc(n * sizeof(SelectWaiter))` on the parking path. A select loop pays
  the first every iteration; unmeasured. Candidate second consumer for a runtime object pool
  (`docs/green-task-pool-v0.md` §7.1, alongside `Chan` and `Scope`, both per-object malloc/free).
- [ ] `P3` **Make `SPROUT_TASK_STACK_BYTES` an env knob** (compile-time `#define` today). Worth 3×
  on the HTTP server for a recursion-depth tradeoff the deployment should own. Also: the 1 MiB
  default has never been measured against real handler depth — measure what depth handlers use.

### Compiler / Stdlib Misc

- [ ] `P3` **Decide when to delete the deprecated brace form of `class`/`instance` bodies (2026-08-16).** The layout form is idiomatic, the whole corpus was migrated, and the brace form is now **deprecated**: `parse_class_body`/`parse_instance_body` still accept `{ … }` (one token of lookahead), and `lint_rules.deprecated-brace-body` reports every occurrence. Since the pre-commit hook fails on any lint finding, new brace code cannot be committed — so the deprecation is already enforced and the open question is only *when the parser support goes*, not whether. Keeping the parser half costs nothing and keeps any out-of-tree source compiling. Prior art is split on ever removing it: Haskell keeps both permanently (§2.7 layout is defined as brace insertion, and the two "can be freely mixed"), Scala 3 likewise keeps optional braces; PureScript documents layout only. Deleting it is a small parser deletion, the §8.5 "Declaration syntax" rewrite, and retiring the lint rule with its tests. **Prerequisite before deleting:** the lint rule is currently the only thing pointing users at the fix, so removal should land the parse error's message with the same guidance the rule gives.
- [x] `P2` **FIXED 2026-08-15. A duplicate top-level `fn` definition is accepted by the typechecker and only caught by the LLVM verifier.** `check_duplicate_fn_decls` now rejects it at the second declaration (`` `twice` is defined more than once in this module ``), keyed on the **name alone** — Sprout has no overloading, so a same-name/different-arity pair is two definitions of one symbol, and that case previously failed at the *call site* with an arity error because the second declaration had shadowed the first in the env. Only `FnDecl` participates: instance/class methods are `InstanceMethodImpl`/`ClassMethodSig`, so the many `fn eq` implementations across instances never collide. No bundling false positive — `decls` is post-bundler, where imported definitions carry their module prefix (confirmed by the compiler still self-compiling with the check active, across its ~40 bundled modules). Spec §5.1; fixtures `duplicate_fn_definition{,_arity}`. Original report follows. Two same-name, same-arity top-level `fn`s in one module pass `--phase check` (reports `OK`, and lists the name once in the env dump) and `--emit-ir` **exits 0**, emitting two `define` blocks for the same symbol. Nothing in the Sprout front end objects; the failure surfaces only if someone runs `opt --passes=verify`, as `invalid redefinition of function 'twice'` — an LLVM error naming a mangled symbol, with no source position and no mention of either declaration site. On the ordinary compile path the invalid IR is handed to clang instead. 5-line reproducer: two identical `fn twice(x: Int) -> Int = x + 1` above a `main` that calls it. Found the hard way while implementing the tyvar-instance-head fix — a new helper was written byte-identical to an existing one 3000 lines up in `infer.sprout`, and the *seed refresh* was what reported it. Wanted: a duplicate-definition check over top-level decls in the same pass family as `check_overlapping_instances`, reporting the second declaration's position. Note the shadowing question is adjacent but separate (prelude-vs-local shadowing, BACKLOG §prelude-bundling item) — this is two definitions in ONE module, where no shadowing rule could make it well-defined.
- [ ] `P3` **Five implementations of "which names does this pattern bind?" (2026-08-14).** `ast_to_ir.pattern_names`, `dce.pat_binds`, `linear_check.pattern_all_binders`, `linear_check.pattern_linear_binders` and `verify_dispatch.pattern_bound_names` all answer the same question over `ast.Pattern`, each with its own copy of the nine arms. All five are now exhaustive, so a new variant is a compile error at every one of them rather than a silent wrong answer — but that is five sites to update in lockstep, and the return shapes differ (`List String`, `Bool`, and a type-directed variant that consults `types.Type`), so a single shared helper is not a mechanical extraction. Worth doing when a pattern variant is next added, which is the moment the cost is actually paid. Not urgent: the exhaustiveness check makes divergence loud, which is the property that matters.
- [ ] `P3` **`looks_like_do_step_start` duplicates `parse_expr`'s notion of "starts an expression", by hand (2026-08-14).** Eighteen `tok_is_*` disjuncts in `parser.sprout`, maintained in parallel with what `parse_expr` actually accepts and with nothing to detect divergence. Each divergence has cost a PR: a float literal and a prefix `!` could not begin a do step (`f100ee6f`), and neither could a `let … in` (`389ef98b`). Wanted: a test that derives one from the other — for every token kind the lexer can emit, assert `looks_like_do_step_start` agrees with whether `parse_expr` accepts a minimal expression starting with that token. **Feasibility unverified**: some tokens are expression-legal only in context, so the test needs a way to avoid false failures, and that design question is the actual work. A derivation test replaces the current pattern of adding one fixture per discovered gap.
- [~] `P1` **Over-application of a function whose return type is a function miscompiles (2026-08-14). DIRECT CALLS FIXED the same day; the VALUE-CALLEE case fixed later the same day; one language-level question remains.** The direct half is closed by "infer: check call arity in both directions": the arity gate was one-sided (`under_application_error` with no counterpart), so surplus arguments slid through structural unification into codegen's silent fall-through. `call_arity_error_for` now reports both directions, and `translate_general_call` errors loudly on over-application rather than falling through. The same change fixed an opposite-sign bug this entry missed — `mk()` where `fn mk() -> Int -> Int` was *rejected* ("requires arguments but was called with none"), leaving that shape uncallable, because the no-argument branch keyed on the result TYPE being a function instead of the declared parameter count; a zero-parameter function returning a function has a scheme identical to a one-parameter function's. Sprout is exact-arity n-ary (`docs/currying-and-pipe-decision-v1.md` §9a.5), so `adder(1)(2)` is the legal spelling and already worked. Tests: `tests/stdlib/test_curried_return_application.spr`, `tests/conformance/type_error/over_application{,_plain}.{spr,err}`.
  **VALUE-CALLEE HALF CLOSED 2026-08-14** by "closures: record each closure's arity and check it at every application" — §8.5 PR 1 of `docs/currying-and-pipe-decision-v1.md`, which that doc had already scoped as common to Packages A and C-a and explicitly safe to land before the A/C-a choice was final. It went unbuilt because §9a.5, selling the package that landed (C-b), claimed it needed "no runtime-arity field" — conflating *no partial-application machinery* with *no arity field at all*. §9 says the opposite ("C-a **also** pays for the arity field and the clean-panic step") and is the one that binds, since C-b is C-a plus placeholders. That sentence is corrected in place.
  The bug was also **wider than this entry recorded**, in two directions, each found by probing rather than reading. It was never specific to an alias: a plain fn-typed **parameter** — `fn apply_two(g: Int -> Int -> Int) = g(1, 2)`, ordinary higher-order code — miscompiled identically. And it was never specific to over-application: **under**-application through a value, `let h = add2 in h(1)(2)`, SIGSEGV'd. That second one is *type-correct* code, since `Int -> Int -> Int` says you may apply one argument at a time.
  The closure now carries its parameter count in the GC header's aux field, packed beside the capture count exactly as `SPROUT_HEAP_OBJ` packs `(tag << 8) | arity` — so no payload slot moved, no size formula changed, and capture indices stayed put. `sprout_closure_arity_check` guards each `IRApplyClosure`; it is a call rather than an inline compare-and-branch because an op that opened a basic block would leave downstream phis naming the wrong predecessor. Gate: `just closure-arity-smoke` — both mismatch directions must panic cleanly, with a positive control pinning that saturated calls through a runtime-chosen value still compute. Statically-known callees are still caught at compile time and never reach the runtime guard.
  **STILL OPEN — what PR 1 deliberately does not do.** The guard *rejects* a mismatch; it does not make one work. `h(1)(2)` for a two-parameter `h` is now a clean runtime error rather than a partial application, so the type `Int -> Int -> Int` still advertises a currying the ABI does not implement. Closing that is §8.3 generic apply (Package A: build a PAP on under-application, saturate-then-reapply on over-application) — which reverses C-b's landed decision that under-application is an error, so it is a language call, not a patch. The alternative is Package C-a's arity-aware types, which would turn both mismatches into *compile* errors and is the only option that makes the type system honest about a value's arity. Either way, the arity field is now paid for. Original entry follows. `fn plain_lambda() -> Int -> Int = \ x -> x + 1` followed by `plain_lambda(1)` type-checks and returns the **closure handle as an Int** — printed `35184372088840`, a pointer value, exit 0, no diagnostic. Wrong code, not a crash. Found while writing `tests/stdlib/test_do_step_starts.spr` (the lambda case had to be rewritten around it); reproduced on `6b1cb598` with no `do` block involved, so it is independent of do-block layout. The typechecker and codegen disagree about arity: because function types are curried, the checker folds the declared `-> Int -> Int` into the parameter list and treats `plain_lambda` as one-ary (calling it as `plain_lambda()` is rejected with "requires arguments but was called with none"), while codegen emits a zero-ary function returning the closure and drops the argument. Either the checker's arity view must reach codegen so the argument is applied, or the shape must be rejected. Needs a runtime-behaviour regression test asserting `plain_lambda(1) == 2`, per "Code and Testing" §6.
- [ ] `P3` **`just fmt` mangles prefix `!` applied to a call (2026-08-14).** `!flag()` is reformatted to `! flag ()` — a space after the `!` and another before the argument list — and in argument position the preceding comma loses its space too: `takes("x", !flag())` becomes `takes("x",! flag ())`. Cosmetic only: verified semantics-preserving (both spellings compile and print the same result) and the output is stable, so `fmt-check` stays green. But it is the formatter emitting code no style guide would accept, and it silently rewrites hand-written source, so the canonical form of an idiomatic `!` negation is currently ugly. Reproduced on `6b1cb598`, unrelated to any recent parser change. Likely `formatter.sprout`'s spacing rule for `UnaryExpr("!")` — contrast unary `-`, which formats as `-5`. Wanted with a `tests/fmt/` fixture pinning `!flag()`, `!x`, and the argument-position case.
- [ ] `P3` **One-line `let … in` is rejected everywhere, and the diagnostic blames the next declaration (2026-08-14).** `fn f(n: Int) -> Int = let x = n + 1 in x + 10` fails with `Expected pattern` pointing at the *following* top-level declaration. §5.2.1 requires `in` "dedented to the `let` column", so this is spec-conformant rejection rather than a bug, and the whole repo writes `in` on its own line — but the one-line form is the canonical ML spelling and the error names a line the author did not write. Root cause: `parse_let_block`'s binding-end scan is line-based (`scan_do_step_end`), so a same-line `in` cannot terminate a binding slice. *(Updated 2026-08-14: the second half of this sentence — that `parse_let_binding_sub` returns a dummy index discarding what is left — described a real defect that has since been fixed on its own; the leftover tokens are now a located error rather than silently dropped, so the one-line form fails loudly at the `in`. The line-based scan is what remains.)* Two parts, separable: (a) fix the diagnostic to point at the `in` and say it must be dedented to the `let` column — cheap and worth doing alone; (b) accept the form, which needs a spec change to §5.2.1 plus a binding-end scan that stops at a `let`-balanced `in`. **(b) is the risky half:** the balancing miscounts when a binding's right-hand side holds a `do` block containing a `let` *statement* (a `let` with no matching `in`), which would then swallow the real terminator. Found while fixing `let … in` as a `do` step (`docs/bug-report-uncharted-suns-2026-08-14.md` §5), where Kuba scoped it out deliberately.
- [ ] `P2` **A local named `empty` — any zero-argument, return-dispatched class method — is still rejected (2026-08-14).** `fn via(empty: Int -> Int) -> Int = empty(7)` fails with `check: No instance of Monoid for Int`, while the same shape named `mk` compiles. Note the **absent `dispatch-verify:` prefix**: this is rejected during dispatch *resolution*, not by `verify_dispatch`, so it is a different pass from the local-shadowing fix that closed the `append`/`to_string`/`compare`/`pure` cases — and it is **not** a 2026-08-14 regression, failing identically on the pre-`a813bd9a` compiler. `Monoid.empty` takes no arguments, so its dispatch is driven entirely by the expected return type, which is the arm a local shadow must be excluded from. Wanted: the scope check the verifier now has, applied wherever resolution decides a bare name is a class method. Deliberately excluded, with a comment saying why, from `tests/stdlib/test_local_shadows_class_method.spr`.
- [ ] `P3` **`parse_do_let_bindings` never reads its `binding_col`, so a misaligned binding is silently absorbed (2026-08-14).** The function takes `binding_col`, threads it through the recursion and compares nothing to it; the split between bindings falls out of wherever `parse_expr` happens to stop. A third binding at a column that is neither the binding column nor the block column is accepted — `let a = 1` / `    b = 2` / ` c = 3` compiles, exit 0. Both the function's own comment ("split by LAYOUT COLUMN — the same rule `parse_let_block` applies") and `docs/spec-v0.md` §5.2.1a ("layout-aligned under the first one") assert an alignment rule that nothing enforces. Two ways out, and which one is right is a language call: enforce the column and reject the misaligned binding (a tightening — needs a corpus sweep), or drop the claim from the comment and soften the spec sentence to say bindings split at the end of each right-hand side. Not a regression: before the multi-binding fix this shape failed with `Unknown variable: b`, which was worse.
- [ ] `P3` **`unresolved_in_types` names the LAST unknown type in a list, not the first (2026-08-14).** `bundler.sprout`'s `Cons t rest -> or_unresolved(unresolved_in_types(rest, declared), t, declared)` recurses tail-first and prefers the tail's result, so `type Foo (..) = | C Bad1 Bad2` reports `Bad2`. Confirmed. Inconsistent with `unresolved_in_type`'s `TypeApply`/`TypeArrow` arms and with `unresolved_in_params`, which are head-first — so which name the diagnostic picks depends on the syntactic position the unknown sits in. Affects constructor argument lists, tuple types and constraint argument lists. Fix is a one-line flip to check `t` before recursing; wanted with a fixture pinning that the *first* unknown is named.
- [x] `P3` **A malformed `let … in` do step reports the pre-fix `Expected pattern` error (2026-08-14) — FIXED the same day**, exactly along the line this entry prescribed. `parse_do_let_in_expr` now returns `Maybe (Result ParseError ast.Expr)`, separating "no top-level `in`, so not this form" from "this form, malformed". The caller still attempts the statement reading in the second case — the `in` prefilter is imprecise on purpose — and reports the expression-form error only when *both* readings fail, so the fallback still wins for a genuine statement whose right-hand side merely contains a nested `let … in` (the case `tests/stdlib/test_do_let_in_step.spr` pins; still green). It stopped being cosmetic immediately: the trailing-token fix below first produced a rejection whose message was the statement path's `Expected = at 14:20`, pointing at the `in` line, and that would have been cemented into a conformance fixture had this stayed open.
- [x] `P1` **A `let` binding silently deleted everything after it on the same line (2026-08-14, found by code review, fixed the same day).** `let a = 1 print("dropped")` followed by `in …` compiled at exit 0 with the call **absent from the emitted IR** — a live statement removed without a diagnostic — while the identical line *without* the `in` was already a loud `Unexpected token after the end of a do step`. So the two readings of the same text disagreed about whether it was an error. Root cause is older than either: `parse_let_binding_sub` computed a perfectly good end index and returned a dummy `0` instead (its `else` arm discarded the clause's end by a second route), and `parse_let_binding_step` ignored even that, resuming at the layout-scanned slice boundary. Nothing could compare where the binding stopped against where its slice ended. It predates `let … in`-as-a-do-step — the function is byte-identical before that commit, which never touched it — and was always live in expression position (`fn f() -> Int = let a = 1 999` / `in a` compiled and returned 1); that change made it reachable from `do`, where `step_fully_consumed` explicitly promises the opposite. It also falsifies a safety invariant stated in that commit message ("a slice the expression parse consumes whole is one that is rejected TODAY"): this slice *was* rejected today, and became accepted with tokens dropped. Fixed by returning the real end index from both arms and requiring each binding to consume its whole slice, giving a located `Unexpected token after the end of a let binding at L:C — a let block takes one binding per line`. Tightening: source with dead tokens inside a `let` binding line now fails to compile, in expression position as well as in `do`. Fixtures: `tests/conformance/parse_error/let_binding_trailing_tokens_{do,expr}.spr`, `let_binding_trailing_after_else.spr`.
- [ ] `P1` Fix closure wrapper calling convention for named functions with tuple parameters: `emit_named_fn_wrapper_lines` generates `(ptr %env, { ptr, ptr } %a0)` but `list_map_go` (and all generic higher-order functions) pass the element as `i64`. Fix: detect tuple param types in `build_wrapper_params` and emit an `inttoptr i64 %a0 to ptr` + `load { ptr, ptr }, ptr %a0_ptr` conversion before calling the named function. Tracked in `Stage0ExecutionTests::test_tuple_fn_as_value` as `xfail` (`_KNOWN_CC_BUG_SHAPES`).
- [ ] `P2` Add `module prelude` header to `prelude.sprout` so all its symbols get `@prelude.` prefix in emitted LLVM IR (2026-05-18): eliminates all future POSIX/libc symbol collisions (current workaround is the `pipe` → `pipe_apply` rename). Requires stage0/stage1 rebuild since bundler and checker both derive the canonical qualified name from the module header. The `pipe_apply` rename is the tactical fix; this is the principled long-term fix.
- [ ] `P2` Generalize `stdin_read_bytes` to `io_read_bytes(fd: Int, n: Int) -> Maybe String !{IO}` once a `File`/`Fd` abstraction is added to stdlib: `stdin_read_bytes` is a thin `fread(buf, 1, n, stdin)` wrapper justified by LSP/DAP Content-Length framing; a proper file-descriptor read primitive would subsume it and enable reading from pipes, sockets, and files without extra builtins. Candidate design: expose `stdin_fd()`, `stdout_fd()`, `stderr_fd()` constants plus `io_read_bytes(fd, n)` — `stdin_read_bytes(n)` becomes `io_read_bytes(0, n)`.
- [ ] `P2` Audit HM inferencer for latent typeclass-constraint / accumulator-type interference (2026-05-20, recharacterised 2026-05-22): the original `fold_indexed` failure was caused by two syntactic barriers — Sprout lambda params don't support tuple destructuring (`fn (i, acc) x -> ...` is not valid), and a nested `match` inside a call-arg position causes parser ambiguity — not a true HM unification bug. The natural `(Int, b)` tuple accumulator works fine once both barriers are sidestepped with a named helper. However, the broader question of whether OutsideIn-style "gather wanted / solve wanted" separation is needed for more complex constraint+accumulator patterns remains open. If a future regression surfaces, audit `infer.sprout` `check_call_constraint` and `inject_constrained_fn_dicts`.
- [ ] `P2` **Honour lambda parameter type annotations in inference (2026-08-05).** `ast.Param String (Maybe TypeExpr)` carries the annotation, but `infer.sprout` discards it: `make_fresh_param_types` allocates a fresh variable per parameter without consulting it, and `extend_env_with_params` drops it. Two-pass argument inference (`docs/lambda-argument-inference-v0.md`) masks this in *argument* position — the callee's parameter slot supplies the type — but nowhere else, so `let wrap_it = \(s: String) -> `<${s}>`` still fails with `use of undefined value '@to_string'`: `s` stays unconstrained, `to_string` has no instance to dispatch to, and codegen emits a direct call to the class method. Spec §5.3 currently carries a "not yet enforced" note for this. Fix needs the enclosing declaration's type-variable environment (`local_vars`) threaded into `infer_lambda_expected` before calling `type_from_ast` — otherwise `\(x: a) -> …` inside a `where`-constrained function turns `a` into a rigid `TConst` instead of binding the declaration's type variable. Seed-gated (compiler source); needs success *and* failure typecheck tests per "Code and Testing" §5.
- [ ] `P2` Fix `assert_eq` (`stdlib/test.sprout`, `where Eq a, ToString a`) being a **silent no-op on `Double`** (2026-07-10): a `Double` `assert_eq` neither prints PASS/FAIL nor increments the counter and execution continues, so any Double `assert_eq` test is a false green. Root cause is the null-filled unresolved-dict class (the `Eq`/`ToString` dict for `Double` resolves to a no-op instead of faulting). Add a regression test (a deliberately-unequal Double case must FAIL) and make the dispatch either work or fault loudly. Workaround today: `assert_true(state, label, x == y)` — Double `==` and `to_string` both work. Found during digit-recognizer Phase B kernel work.
- [x] `P2` **`Double`→`Int` conversion (`to_int`/`fptosi`, the inverse of `to_double`). CLOSED 2026-08-14** — the third copy of this item (with the `P1` in §Language/runtime and the `P3` in §math, both closed the same day); shipped as `math.to_int : Double -> Maybe Int` + `to_int_or` over `double_to_bits`, so the `fptosi` intrinsic and `ir_header` declare this entry specifies were not needed. Rounding mode is answered by keeping it out of the conversion: `floor`/`ceiling`/`truncate`/`round` are total `Double -> Double`, and `to_int` truncates toward zero. Design: `docs/double-to-int-v0.md`. Original entry follows. `to_double(Int) -> Double` landed 2026-07-06 as a compiler intrinsic (`translate_call` intercepts the name and inlines `IRSIToF` + a bitcast, in both codegen paths at the time); the reverse direction was explicitly left TODO in the same landing. Needs a new IR op mirroring `IRSIToF` (bitcast the i64-carried bits to `double`, `fptosi` to `i64`) plus the matching intercept + `ir_header` declare. Truncation semantics need deciding (round-toward-zero per C `fptosi`, vs round-to-nearest) and documenting once `Double` gets a normative spec section (see below). Design doc: `docs/nn-gap-analysis.md` §7.
- [ ] `P2` **`String`→`Double` parsing.** No `parse_double : String -> Maybe Double` (or equivalent) exists — `Double` currently has no textual round-trip in the direction a program needs for reading numeric input/config/JSON values. Mirror the existing total `parse_int : String -> Maybe Int` (`stdlib/prelude.sprout`). Surfaced as a remaining float gap alongside the `to_int`/`fptosi` item above when `Double` landed 2026-07-06.
- [ ] `P3` **Document `Double` normatively in `spec-v0.md`.** `Double` landed 2026-07-06 but the spec only acknowledges it as an experimental extension (§8.4, reworded to drop the stale "no floating-point in v0" claim) plus a `ToString` row in the §8.5 table — there is no normative section defining `Double` literals, the concrete-only same-type arithmetic rule (no implicit `Int`/`Double` coercion), ordered/equality comparison, or `to_double`. Add one once the type graduates from experimental. Wider numeric tower / `Float` (f32) tracked separately in `docs/numeric-types-v1-draft.md`.
- [ ] `P2` Allow a layout `do` block inside call parentheses — i.e. an inline multi-statement effectful lambda as a call argument (2026-07-11): `range_fold(\ (s, k) -> do <newline> stmt1 <newline> stmt2, seed, r)` fails with "Expected )"; today the lambda must be `let`-bound and passed by name. Verified with probes that this affects *all* argument positions (first, last, and explicitly parenthesized) and single- vs multi-statement — it is not a non-final-argument or trailing-comma issue. Root cause in `stdlib/compiler/parser.sprout`: the do-step layout scanner ends a block only on EOF or a dedent (`col < block_col`) — `scan_do_step_end` (~L434), `collect_do_steps` (~L480) — never on a bracket, and `update_bracket_depth` (~L423) clamps close-brackets at zero (`if depth > 0 then depth - 1 else 0`), so a `)` that closes an *enclosing* `(` is invisible and the block over-consumes to EOF. Fix (standard layout rule "an explicit close bracket ends an implicit layout context", cf. Haskell's layout parse-error rule): let depth go negative and end the current step + the do-block when a closer would take depth below 0 (and on a depth-0 `,`, which inside a do-block can only mean an argument list). Especially wanted because the data-last combinator convention (`range_fold(step, init, r)`) puts the lambda in the first, most natural inline slot. Sibling of the "nested `match` in call-arg position" ambiguity noted above. Needs failing parse tests → 3-function fix → seed refresh + full gate chain (layout is delicate; guard against regressions in existing do-blocks, which never reach negative depth). Found during digit-recognizer verbosity cleanup (PR #161).
- [ ] `P3` Re-add `SPROUT_TIME_PHASES` per-phase compile timing on the typed path (2026-07-12): the direct-codegen retirement deleted `compile_full_ir_lines` and its `compile_phase_recheck_timed`/`format_phase_*`/`PhaseTimes` machinery, which emitted the `[phase] bundle=… check=… lower=… codegen=… total=…` stderr line. It was only ever wired to the (now-gone) `--use-direct-codegen` path, so nothing regressed on `--emit-ir` — but the diagnostic is genuinely useful. Re-add: wrap `time_now_micros()` around `ir_pipeline.compile_program_streaming` (and the recheck phases) in a timed variant of `run_file_use_ir_codegen`, gated on the same env var.
- [ ] `P3` Implement Sprout source-level DWARF on the typed path — `--emit-ir --debug` is currently a no-op (2026-07-12): the direct backend was the only path that honored `--debug` (DWARF metadata in a 4th IR section). Post-retirement the flag is accepted but ignored; `just build-debug` still produces an lldb-loadable binary via clang `-g -O0` (LLVM-level line info), just without Sprout source attribution. Re-adding source-level DWARF on the typed pipeline is net-new work in `ast_to_ir`/`ir_lowering` (emit `!DILocation`/`!DISubprogram` metadata keyed to source positions). **Supersedes/merges with the broader `--debug` debugger item under "V1 Roadmap Candidates" (§ DWARF emission).**
- [ ] `P4` Collapse the `--use-ir-codegen` flag onto `--emit-ir` (2026-07-12): with `--use-direct-codegen` gone, `--use-ir-codegen` is a pure synonym for `--emit-ir` (both route to `run_file_use_ir_codegen`) — it existed only for the A/B differential. It survives because justfile recipes call it (`~803/961/1007/1048`). Repoint those recipes at `--emit-ir` and drop the alias arm from `compile_driver.main`.
- [ ] `P3` **Type-driven-design gaps from the "parse, don't validate" / "make illegal states unrepresentable" audit (2026-07-30).** Sweep against `docs/guidelines.md` §3/§4 across `stdlib/` and `stdlib/compiler/`; adherence is strong (refining compile pipeline `String → Vec Token → Program → TypedProgram → IRProgram`, `wrap` taxonomy in `source.sprout`) but these concrete illegal-state-representable / validate-not-parse spots remain. Everything under `stdlib/`/`stdlib/compiler/` needs a full `just test` and, for compiler-source, a bootstrap seed refresh + design approval — none is a drive-by. (The loam-engine items from this audit moved to the uncharted-suns BACKLOG when loam was extracted; the landed `GroupId` record is retained below.) `Ord.compare` returning an `Int` sentinel instead of `Ordering = LT|EQ|GT` is the highest-value gap and is **already tracked** as its own `P2` item above ("Revisit `compare` return type").
  - *Compiler (`stdlib/compiler/`, seed-gated):* (a) `Token TokenKind String pos` (`token.sprout:19`) lets kind and payload disagree — `Token(TokenIntKind, "hello", pos)` is representable; contrast the `Expr`/`Pattern` ADTs where each variant carries its own typed payload. A per-kind payload (or splitting text-bearing kinds) closes it. (b) Operators are raw `String` in the AST — `BinaryExpr String …` / `UnaryExpr String …` (`ast.sprout:77-78`), parser stuffs arbitrary `token_text`; a closed `BinOp`/`UnOp` ADT would make bogus operators unrepresentable. (c) `is_function_scheme`/`is_polymorphic_scheme -> Bool` (`analysis_service_driver.sprout:820,825`) are boolean-blind; a `SchemeShape = MonoValue | PolyValue | FnValue` classifier is more honest. (d) Scalar-ness recomputed downstream by string-matching the type name (`type_kind.sprout:52`, `field_kinds.sprout:40`) instead of being carried on the `Type` ADT — a "validate, don't parse" miss.
  - *Stdlib:* `NodeInterp (Vec String) Bool` (`template.sprout:24`, the `Bool` is `is_safe`) → `Escaping = Safe | Escaped`; smallest diff of the set, one file, ~5 sites, but `stdlib/` gate = full `just test`.
  - *loam (landed record — open loam items moved to the uncharted-suns BACKLOG when loam was extracted):* **[LANDED 2026-07-30]** `group_of` now returns a `GroupId` newtype (`agent.sprout`), threaded to the genuine group-id consumers `home_x`/`home_z`/`group_angle` in `ecs_flocking` (typed on `GroupId`, so an entity index or a raw count can't be passed there), unwrapped via `group_index` only at leaf `mutvec` index sites. Zero-cost (`wrap` erases to i64, IR-verified). **Scope note:** this is *boundary* typing, NOT index-site swap protection — once unwrapped to index a `MutVec Int` the group/entity distinction is gone, so the earlier "exactly the crossing `scene.sprout:36` warns about" framing was wrong. True per-container index safety needs a group-indexed container type (a separate axis; the `MutVec` substrate is shared by entity and group arrays alike). The open loam gaps (typed model/shader handles, a `resting` enum cell, a `Clips` named record) are now tracked in uncharted-suns.
- [ ] `P3` Replace per-arity `ToString` tuple instances with a generic/variadic approach (2026-06-07): PR 2.5 added explicit `instance ToString (a, b)` through `instance ToString (a, b, c, d, e)` for arities 2–5 in `stdlib/prelude.sprout`. The five instances are correct and have full test coverage, but each arity requires a separate declaration. A variadic approach would require either (a) a type-level natural-number index over tuple arities (type-indexed HList/heterogeneous-list style), or (b) a macro/deriving mechanism that generates instances up to a compiler-defined max arity, or (c) language-level variadic typeclass support. The current approach is pragmatic: 6-tuples and above are uncommon in Sprout code today, and any of the three alternatives requires substantial new language machinery. If 6+ tuple arity becomes needed before variadic support lands, add the instance explicitly following the established pattern in `prelude.sprout`.

### CI / Build Performance

- [ ] `P2` **CI wall time grew ~8 → 15–20 min ("slower and slower")** — investigated 2026-07-22, no code landed (findings recorded, build paused by Kuba's call). **NOTE (2026-07-31): all measurements below were taken on the former self-hosted GCE worker. CI now runs on GitHub-hosted `ubuntu-latest` — wall times and the worker-capacity levers are superseded; retriage against the new runner before acting.** Two independent effects, kept separate:
  - *Monotonic creep (the "slower and slower"):* the stdlib test corpus grew 101 (Jun 25) → 144 (Jul 8) → 211 (Jul 21). `test-stdlib-stage1` is CPU-bound — 137s wall but **13.5 min of CPU** at 8-wide on an 11-core box locally — and scales ~linearly with test count. The hogs are the **18 heavy compiler-importing tests** (each re-emits the whole compiler, ~16s emit-IR locally), not the 153 prelude-only tests (~0.5s each).
  - *Bimodal ~7-min spikes (→21 min):* stage-1 build-cache miss. The `actions/cache` key is `hashFiles('bootstrap/compile_driver.ll', 'runtime/sprout_runtime.c')`, and nearly every compiler PR refreshes the seed → new key → `bootstrap-from-seed` re-runs `opt --passes=verify` + `clang -O2` over ~271k IR lines. Confirmed via a natural experiment: commit `afff944` ran 21.2 / 20.2 / 14.4 min (runs 758/759/760) on byte-identical code as the cache warmed. `setup` (the apt-get job) is a red herring — ~1s every run; the GCE host is persistent.
  - **Deeper lever (multi-day):** the `.iface`/`.bc` precompiled-modules feature — infra (serialization, `--emit-iface`, `refresh-iface`) shipped; the speedup wiring is unbuilt. PR 2 (bundle-skip) is the right first increment at ~4s/compile, medium risk. Full refreshed measurements + revised ROI in [docs/iface-precompiled-modules-v1-draft.md §"Refreshed measurement (2026-07-22)"](./docs/iface-precompiled-modules-v1-draft.md).
  - **[LANDED 2026-07-28] PR-gate the compiler tests (cheap mitigation of the creep).** Re-profiled: `test-stdlib-stage1` is **1074s user-CPU (79% of the `test` job)**; the **58 `tests/stdlib/compiler/` suites = 846s = 61%** of emit-CPU (14.6s/file, each re-bundling the whole ~260k-line compiler; emit scales ~linearly, exponent 1.12 — not a quadratic). Split `_test-stdlib` on a `dirs` arg → `test-stdlib-core-stage1` (171 files, always runs) + `test-stdlib-compiler-stage1` (58 files). CI (`.forgejo/workflows/ci.yml`) gains a **Detect compiler-affecting changes** step that runs the compiler suite only when a PR touches `stdlib/ runtime/ bootstrap/ tests/stdlib/compiler/ justfile .forgejo/` (conservative dependency surface — the compiler pulls in the prelude, is built from the seed, links the runtime); **fail-open** on any non-PR event or detection error. master + nightly always run the full set, and `just test`/`test-stdlib-stage1` are unchanged (DoD #5 intact). Directly cuts the docs/examples/gfx-only PRs (which dominated the recent-days traffic) by ~846s CPU.
  - **[LANDED 2026-08-08] The single biggest cause was a quadratic `strlen` in the runtime, not test-corpus growth.** `str_starts_with_at_byte` and `str_slice_bytes` each opened with `strlen(s)` over the whole string just to bounds-check the caller's byte offset, so every call was O(|s|) and every byte-cursor scanner built on them was O(|s|²). `lexer.try_ops` probes 13 multi-char operators at *every token position*, so lexing `ast_to_ir.sprout` (452 KB) scanned ~4e11 bytes; `sample` put **86% of a `--phase bundle` run in `_platform_strlen`**. Fixed by reading the O(1) CSTR-header length (`sprout_cstr_byte_len`) that `str_byte_len` already used. Measured on one harness, 271/271 passing before and after: **1664s → 1051s CPU (−37%), 301.7s → 226.4s wall (−25%)**; whole `just test` ~460s/~1840s → 322s/1192s. Bundling `ast_to_ir.sprout` 6.99s → 0.72s. **Correction to this item's numbers:** the "emit scales ~linearly, exponent 1.12 — not a quadratic" note above measured scaling *across files of differing size*, where every compiler test bundles the same whole compiler and the per-file cost is therefore near-constant; it did not measure scaling *within* a growing file, which was ~n^1.9 (8000 synthetic decls: 8.08s → 0.69s after the fix). The 846s compiler-suite figure should be re-measured before it is used to size any further work. Guarded by `tests/stdlib/test_byte_offset_cost.spr`, which asserts cost is independent of string length rather than checking a wall-clock budget.
  - **[FOLLOW-UP] Straggler heavy bundlers still on every PR.** Directory gating misses the ~10 `tests/stdlib/test_ir_*` suites that ALSO bundle the whole compiler (e.g. `test_ir_codegen_string_pattern.spr` = 222k IR lines / ~17s emit) but live in the flat `tests/stdlib/`, not `compiler/`. Either move them under `tests/stdlib/compiler/` (or a `tests/stdlib/ir/` gated the same way), or gate by an explicit file list. Also open: LPT (largest-first) dispatch in `_test-stdlib`/`_compile-examples`/`ci-fast-gates` to stop a 50s pole stranding idle lanes; and folding the serial `verify-bootstrap-fixed-point` (~23s) into the `ci-fast-gates` fan-out to overlap it.

- [x] `P3` **`bench/*.sprout` is compiled by NOTHING — not `compile-examples-stage1`, not `gate`, not
  CI. FIXED 2026-08-11** as `just compile-bench`, wired into `gate` and into CI as its own step. Rather
  than duplicate the pipeline, `_compile-examples` grew `srcs`/`label` parameters, so bench files get
  the identical emit-IR → `opt --passes=verify` → link treatment examples get; benches are compiled and
  linked but **not run** (they are deliberately long-running, and the value is that they keep
  type-checking as the language moves). All 7 pass. Two costs this had already imposed, both found by
  hand: the `P0` descriptor leak lived here, and `bench/unboxed_read` needed migrating for the
  fallible-bind rule. The recipe now also **fails on an empty glob** — a stale path would otherwise
  report "All 0 compiled OK", which is the same silence in a new costume. `gate-audit` still passes.
  <details><summary>Original report</summary>

  The justfile does not mention `bench/` at all; each `bench/<name>/bench.sh` builds its own
  sources when a human runs it. Found 2026-08-11: the descriptor leak in
  `bench/http_worker_pool/{pool,spawn}_server.sprout` (BACKLOG `P0`, the linearity facet) sat there
  because nothing type-checks or runs that code in the normal loop. Unlike the `c-runtime-test` /
  `b1-gate` orphans, this is not a gate claiming coverage it lacks, so `gate-audit` Assertion D
  correctly does not flag it — bench code makes no verification claim. It is still code that rots.
  Cheapest fix: extend the `_compile-examples` corpus (or add a sibling recipe) to emit-IR every
  `bench/**/*.sprout`, which is compile-only and costs seconds. Running them is a separate question and
  probably not worth CI time.
  </details>
- [x] `P2` **No local gate could run Linux, so the poll backend CI uses was never exercised before push. LANDED 2026-08-11** as `just linux-smoke` — runs `task-io-smoke` inside a pinned Linux container against the working tree, under CI's `SPROUT_GC_HDRCHECK=1`. Filed and fixed the same day two Linux-only failures reached CI hours apart: (1) `task_sleep` arms a **timerfd** on Linux — a file descriptor — but an `EVFILT_TIMER` on the already-open kqueue on macOS, so a descriptor-exhaustion back-off written with `task_sleep` needed the very resource it was recovering from, and its arming failure is deliberately process-fatal (`BACKLOG:409`); (2) `accept(2)` on Linux passes already-pending network errors (`ENETDOWN`, `EPROTO`, `EHOSTUNREACH`, …) through to the caller while BSD does not, so the branch handling them is unreachable on macOS. Both were green on every local gate. **Verified red, not assumed:** the shipped recipe reproduces the failed CI run at `4dcfad79` verbatim (`runtime error: builtin task_sleep: could not arm a timer (descriptor exhaustion?)`, exit 1) and is green at `681a9fe8`, which fixed it. Cost ~2m10s cold vs a ~13min CI round-trip. Image is `ubuntu:24.04` + apt `llvm clang` — deliberately mirroring the CI step rather than pinning a third-party clang image, so it drifts *with* `ubuntu-latest` (measured identical: clang 18.1.3, glibc 2.39); a version-locked image would eventually certify a toolchain CI no longer uses, which is the exact failure mode the gate exists to prevent. Three mechanics are load-bearing and documented at the recipe: `--set build_dir /tmp/build` (a container writing into the shared `build/` leaves a **Linux ELF that the host's mtime-only staleness guard reports as up to date** — every host gate then fails to exec a binary the guard refuses to rebuild; CI dodges the same trap by keying its stage-1 cache on `runner.os`), `--tmpfs /tmp:rw,exec` (docker's `--tmpfs` defaults to `noexec`, breaking both just's shebang recipes and every compiled fixture), and a `:ro` repo mount so no container-root file can land in the tree. Not wired into `gate`/`ci-fast-gates`: CI is already Linux, and a container runtime is not a required contributor dependency.

- [ ] `P3` **`linux-smoke` covers OS asymmetry but not ISA asymmetry.** The container is the host's architecture (aarch64 on an Apple-silicon Mac) while CI is x86_64, so the gate certifies the epoll/timerfd backend, Linux errno semantics and glibc — the axis that has actually broken twice — and says nothing about ISA-dependent behaviour (alignment, `long double`, atomics, any codegen difference). Forcing `--platform linux/amd64` is deliberately *not* done: it routes every compile through QEMU, and a gate slow enough to skip is a gate that does not run. If ISA divergence ever bites, the cheap increment is an opt-in `just linux-smoke-amd64` accepting the emulation cost, run before release tags rather than per-push — note `.github/workflows/release.yml` already builds an aarch64 artifact, so both arches ship and only one is smoke-tested locally.
  **CORRECTION (2026-08-11): the framing above is backwards, and the real gap is in CI, not here.**
  `linux-smoke` adds no OS coverage over CI at all — `ci.yml` is `runs-on: ubuntu-latest` with
  `SPROUT_GC_HDRCHECK: "1"`, and `ci-fast-gates`' GATES array already contains `task-io-smoke`, so CI
  runs the identical recipe on the identical OS under the identical env. `linux-smoke`'s whole value is
  **pre-push latency** (~2m local vs a ~13min round-trip), exactly as the entry above it says. What it
  covers that CI does *not* is the **architecture**: the container is aarch64, CI is x86_64. So the
  scheduler, epoll/timerfd and GC are smoke-tested on arm64 Linux **only on a contributor's Mac, by an
  opt-in gate** — while `release.yml` publishes `sprout-linux-aarch64` from a `ubuntu-24.04-arm` runner
  that builds the binary and never runs `task-io-smoke` on it. Fix is cheap and does not need QEMU:
  GitHub's `ubuntu-24.04-arm` runner is free on public repos and already in use by `release.yml`, so add
  an arm64 job (or a `runs-on` matrix) to `ci.yml` — or at minimum run `task-io-smoke` in
  `release.yml`'s existing aarch64 job before uploading the artifact. `just linux-smoke-amd64` under
  QEMU is then unnecessary: the arch CI lacks is the one the local gate already provides.

- [x] `P3` **`loud-fail-smoke` is stale and not CI-gated. DUPLICATE — collapsed into the two `P1` entries above, both fixed 2026-08-07.** Filed 2026-07-23 during records PR1; the same defect was independently re-diagnosed a fortnight later and filed again as a `P1`, which is itself the useful signal: a correct diagnosis sat in the backlog at `P3` for two weeks while the gate stayed red, so the priority, not the analysis, was the failure. This copy had also gone stale in a way that would have mis-scoped the fix — it cites `.forgejo/workflows/ci.yml` as the CI config, from before the move to GitHub Actions (`.github/workflows/ci.yml`), so anyone working from it would have looked in a file that no longer exists. It did get one thing right that the `P1` copy missed: that `gate` aborts the local battery before `test`/`compile-examples`/`test-stress` run, which is why a gate CI never runs is worse than merely un-run. Both observations are now folded into the `P1` entries.

- [ ] `P3` **Flatten pre-existing `staircase-of-doom` sites exposed by the records PRs** (found 2026-07-23 during records PR1/PR2; committed around them with `--no-verify` since they're pre-existing master debt and the lint hook is the local `.githooks/pre-commit` only, not CI-enforced). `infer.sprout`: `resolve_obligation`-family dispatch resolution (pure — the `let..else` candidate), `infer_range`, `typecheck_fn_decl` body. `lowering.sprout`: two `let..else`-candidate sites (~L1021, ~L1079). `driver.sprout`: one "several layers deep" site (~L311). All verified pre-existing on `HEAD` (linted the committed versions); the records PRs only surfaced them by touching those files. Note these are **not** `let..else`/`if..else` sugar swaps: `infer_range`/`typecheck_fn_decl` thread `InferResult`/`Result` inside effectful `do` blocks and every failure arm binds and uses the error (`| Err e -> InferErr(pos, "…" ++ e)`), which `let..else` (pure body, unwrap-or-constant-default per `docs/idiomatic-sprout.md`) cannot express. The behavior-preserving fix is **helper-function extraction** — the existing `infer_if → infer_if_checked → infer_if_merge` split pattern — which adds a call boundary, so it changes the IR and needs a real `refresh-seed` (not fingerprint-only). Do as its own commit; verify via the full test suite (inference is heavily exercised).

#### Runtime-invariant confidence tooling

> From [docs/runtime-invariant-confidence-v0.md](./docs/runtime-invariant-confidence-v0.md),
> the confidence doc written alongside the string-header migration (`byte_length` → O(1) for all
> Strings). Two of its six levers already landed — **HDRCHECK-in-CI** (`SPROUT_GC_HDRCHECK=1` at
> `test`-job level in `.forgejo/workflows/ci.yml`) and the **run-tier example canary**
> (`run-example-canary` ∈ `ci-fast-gates`). Its **typed-IR / Core-lint verifier** lever is the same
> work already tracked as *Dispatch Soundness item 1, Phase 2b* below (do not duplicate). The
> remaining three levers are filed here, ordered by leverage.

- [ ] `P2` **ASan/UBSan build of the C runtime in CI.** A safety-first language's runtime should run its tests under sanitizers: a stray `payload-8` read on a bare string, or any pointer/UB error the HDRCHECK strlen-assert doesn't cover, would be caught immediately with a located C stack trace instead of surfacing as a silent wrong value or a downstream SIGSEGV. **Infra already half-exists:** `just build-stage2-asan` (justfile ~L577) builds the stage-2 compiler with `-fsanitize=address,undefined`, but (a) it only *builds*, it doesn't run the stdlib/conformance suites under the sanitized binary, and (b) it is not wired into `.forgejo/workflows/ci.yml`. Work: add a recipe that runs a representative test + canary set against the ASan/UBSan-instrumented runtime, then a CI step for it. **Constraint (measured):** the CI worker is a single on-demand GCE `e2-standard-4` (4 vCPU / 16 GB, see "CI wall time" item above); ASan roughly 2× slows and 2–3× fattens memory, and the box is already memory-tight at capacity 2. So this likely belongs on the **nightly** cron (the workflow already has a `schedule:` trigger) rather than every PR, or scoped to a small curated fixture set on PRs.

- [x] `P3` **Golden-IR corpus diff for codegen changes. LANDED 2026-08-06** (gate wiring; corpus and scripts predated it). A codegen change's blast radius is now *visible and reviewable* rather than trusted: `just ir-golden-diff` byte-diffs `--use-ir-codegen` output for 57 corpus files (`examples/*.sprout` + `tests/smoke_shapes/*.spr`) against committed goldens in `tests/golden/ir/`, and runs in `gate` + `ci-fast-gates`; `just ir-golden-snapshot` refreshes. Design questions the item raised, as resolved: golden storage is **committed whole `.ll` files** (not inline `CHECK` lines) because the review value is seeing the *entire* diff; **no normalization** — the diff is byte-exact, accepting golden churn on every intentional codegen change as the price of catching unintended ones; refresh ergonomics are the snapshot recipe. Cautionary note for whoever extends this: the corpus and both scripts were written and 57 goldens committed, but nothing ever *invoked* them and this item was never closed — so the corpus silently rotted for months while reading as coverage. `gate-audit` assertion B now fails on any `scripts/*.sh` that nothing invokes, so that specific failure mode cannot repeat.

- [ ] `P2` **`test-stdlib-stage1` does not depend on `bootstrap-from-seed`, so it can silently test a stale compiler.** `test-stdlib-stage1`/`test-stdlib-core-stage1`/`test-stdlib-compiler-stage1` and the three `_test-reject` gates all take the stage-1 binary as a path argument and only check `[[ -x ]]` — unlike `test-conformance-run` and `test-package-resolution`, which declare `bootstrap-from-seed` as a `just` dependency. Consequence: after switching branches (or editing `runtime/*.c` without rebuilding), `just test` happily runs the whole suite against whichever binary happens to be in `build/`. Hit for real on 2026-08-08: a branch switch reverted `runtime/sprout_runtime.c` to its unfixed state while `build/compile_driver_bin_stage1` still contained the fix, and the suite reported green for a compiler that was not in the working tree — measurements taken in that state were off by the whole size of the fix. `bootstrap-from-seed` already has a cheap mtime freshness guard (a few `stat` calls when everything is current), so adding it as a dependency to the stdlib/reject recipes costs ~nothing and closes the hole. Until then, treat `just bootstrap-from-seed` as mandatory after any branch switch or runtime edit.

- [ ] `P2` **Audit every `extern fn` for C-definition/IR-declaration signature mismatches.** Fixing the `_Bool`-vs-`i64` Bool-return bug (2026-08-08, `docs/compiler-internals.md` §"Bool-returning externs") closed four instances of one such mismatch, but nothing *checks* the general property: the C definition's types in `runtime/*.c` are never mechanically compared against the `declare` that `ir_lowering` emits from the `extern fn` signature. This is now the **second** silent extern-ABI mismatch found by accident — the first was CPR width-3 needing `sret` (`native_set_to_list` silently returned `Nil` for months). Both were invisible to `opt --passes=verify` and to linking, because LLVM only sees the `declare`. Two increments: (a) a script that parses each `extern fn` in `stdlib/*.sprout`, derives the expected C signature, greps the matching definition in `runtime/*.c`, and fails on a mismatch — wire into `ci-fast-gates`; (b) longer term, generate the C prototypes from the Sprout `extern fn` declarations so the two cannot drift. Note (a) also protects against the *reverse* audit hole the Bool bug exposed: `scripts/check_approved_builtins.sh` greps `long long <name>(`, so any builtin defined with a different return type is invisible to `runtime/APPROVED_BUILTINS` too — four builtins had escaped that allowlist for their whole history for exactly this reason.

- [ ] `P3` **Transactional bootstrap (never destroy the last-good stage-1).** A failed bootstrap can currently delete the only working stage-1 binary (see the builtin-removal bridge in `docs/debugging.md`), leaving no way forward but the committed seed. Bootstrap should stage the new binary to a temp path, verify it (fixed-point + a smoke) before swapping it into place, and keep the previous binary as an easy-rollback `.last-good`. Turns a bricked local checkout into a one-command restore. Adjacent to the 2-step bootstrap protocol; lower leverage than the CI gates above because it protects the *developer loop*, not correctness of landed code.

### Dispatch Soundness & Diagnostics

> Motivated by the `vec_sort_by` projection-sort crash (PR #176, 2026-07-13) — a
> dictionary mis-resolved to the element type instead of the projected key type,
> a silent runtime SIGSEGV rather than a compile error. Full analysis:
> `docs/retro-dict-dispatch-soundness-2026-07-13.md`. Ordered by leverage.

- [x] `P2` **Missing SUPERCLASS instance silently null-fills a forwarded dict (should be a compile error) — DONE 2026-07-27, commit `f735948`** ("infer: reject an instance missing its superclass instance"). Landed the SAME DAY this item was filed: `check_missing_superclass_instances` + helpers in `infer.sprout` (~3727), wired in `typecheck_decls` right after `check_overlapping_instances` (~3461), decl-scoped so it covers prelude and user instances and only reachable instances are seen. Regression test `tests/conformance/type_error/missing_superclass_instance.{spr,err}` (asserts `No instance of Sup for Widget, required as a superclass of Sub`); seed refreshed. **Residual (minor, P3):** the error is emitted with `dummy_pos()` (`infer.sprout:3462`) — unlocated; the conformance harness matches message text, so nothing fails. Threading the `InstanceDecl`'s real `SourcePos` (its 4th field / `ast.decl_pos`) through `check_inst_supers_go`→`superclass_check_one`→`check_super_list` would point the caret at the instance decl (GHC parity); deferred as a compiler-source change (reseed + full gates) for a cosmetic caret with no message-visible effect. Original spec follows. A class declared `class C f where S f` (e.g. `Applicative f where Functor f`) can have an `instance C T` **without** the required `instance S T`, and it compiles cleanly. Direct method dispatch on the instance works (only the instance's own method dict is needed), but a *forwarded* dict — a constrained fn (`map3`/`map4`/`map5`, `where Applicative f`) that threads the FULL super-expanded dict (Applicative pure/map2 **+ Functor fmap**) — has an unresolvable superclass slot that lowering null-fills (`__unresolved_Functor_<T>` → `ast_to_ir` zero → SIGSEGV at the call). This shipped in #265: `instance Applicative (Result e)` with no `instance Functor (Result e)` → `map3(f, Ok, Ok, Ok)` crashed while `map2` worked (fixed by adding the Functor instance). The compiler should reject an `instance C T` whose superclass `instance S T` is absent, at the instance site, with `No instance of S for T (required as a superclass of C)` — the same "precise-or-loud" principle as the Dispatch verifier items below and the `unsatisfiable_return_position_constraint` fix (§ above). Likely home: `verify_dispatch` or an instance-registration check in `infer.sprout` that walks each instance's class superclasses against the bundled instance set.

- [x] `P2` **Make the unresolved-dict SINK loud, not a silent null-fill — DONE 2026-07-29** (branch `soundness/loud-unresolved-dict`). The last-line-of-defense companion to the superclass check above and item 3 below: those guard the *producers* of an invoked-unresolved dict; this hardens the *sink* so any future producer regression fails loudly regardless of route. `lowering` mints an `__unresolved_<Class>...` sentinel for a free-tyvar dict (provably never invoked — `test_unresolved_dict_nullfill.spr`); `ast_to_ir` used to null-fill it with a bare `i64 0` (`project_typed_codegen_unresolved_dict_nullfill`). If the "never invoked" invariant is ever violated (as it WAS by the missing-superclass bug, PR #268), applying that null closure is a SILENT SIGSEGV. Fix (`ast_to_ir.sprout`, `translate_expr` `__unresolved_` branch): emit a **poison closure** — `IRMkClosure` over a synthesized thunk (`synthesize_unresolved_poison_thunk`) whose slot-0 code pointer PANICS (`IRPanic`, reusing `@panic`) with a located message. Because the closure ABI applies via `load code from [handle+0]; call code(...)`, applying the poison runs the thunk → loud abort; a never-applied poison is inert, so the legitimate dead-dict case is byte-for-byte behavior-preserving. **No runtime change, no new builtin.** Test `tests/stdlib/test_unresolved_dict_poison.spr` (IR-shape tripwire: RED→GREEN verified by reverting the branch — feeds a bare `__unresolved_` sentinel to `translate_program` and asserts the thunk + panic message are emitted, not `i64 0`); `test_unresolved_dict_nullfill.spr` stays green (incl. `SPROUT_GC_STRESS=1`). Full `just test` + fixed-point + smoke-shapes/bundle-smoke + compile-examples + canary all green; seed refreshed (fixed point iter 2). **Scope (like item 3): this is an invariant-assertion NET — the producers are guarded, so there is no source-level RED that INVOKES a poison; the tripwire pins IR shape and the apply→panic behavior is confirmed by the closure-ABI reading. A behavioral apply test (hand-built program that applies a dict param bound to the poison) is possible future work.**
- [~] `P1` **(item 1) Core verifier for dictionary passing — PHASE 1 + PHASE 2a DONE 2026-07-13; phase 2b (IR-level) pending.** The highest-prevention-leverage item. Dictionary mis-resolution (this bug, #141, the `++`/`mconcat` null-fill in `project_semigroup_append_dispatch_fix`, return-type-dispatch bugs) is currently a *runtime* SIGSEGV/corruption because the elaborated dict-passing is never type-checked. The compiler already emits typed IR (the PR11 typed-codegen campaign). Add a verifier pass at the constrained-call boundary: **the dictionary argument threaded for a constraint `C k` must have a head type matching `k`'s resolved type at that call site.** For the concrete case (dict head vs the resolved constraint type) this is tractable without a full System-F re-check; the smoking gun in the motivating bug was IR-visible (`Ord (Int,Int)` threaded where `Ord Int` was required). Prior art: GHC Core-lint — dictionaries are explicit terms and the Core type-checker rejects ill-typed evidence. **Acceptance criterion:** a synthetic program that threads the wrong-headed dict for a constraint is rejected at compile time with a located diagnostic, and the pass is wired into a required gate. Retroactively guards every dispatch fix already landed. **PHASE 1 DONE 2026-07-13** (`stdlib/compiler/verify_dispatch.sprout`, run in the check phase, default-fatal): a post-resolve pass that re-derives each constraint var's type from the callee's SOURCE signature (written params + `where`-clause) matched one-directionally against the concrete arg types — genuinely **independent** of the resolver (never touches the generalized scheme / `prog_to_fresh` / `@constrained` markers), so it also self-guards a `canonicalize_constrained_markers` regression. Rejects a call whose injected `TDict` head disagrees with the derived truth; `EvForward`/polymorphic/no-source-sig/indirect-callee are skip-and-log (`SPROUT_VERIFY_DISPATCH_STATS`). Verified clean across the corpus + compiler source (`mismatched=0`, `verified` 2–60/file) before the fatal flip; escape hatch `SPROUT_VERIFY_DISPATCH_OFF`. Unit test `tests/stdlib/compiler/test_verify_dispatch.spr` (RED-verified: catches the #176 wrong-headed dict, passes the correct/polymorphic/underdetermined/return-position cases); gate `just verify-dispatch-smoke` (active + no-false-positive) in CI. **Phase-1 scope (precise):** catches mis-resolution **where the call's value arguments fix the constraint variable to a concrete type** — this is #176 (the projected key `k = Int`), empirically confirmed (`verified=2`). **Explicitly OUT of phase-1 scope, skipped not verified:** (a) forwarded/polymorphic dicts inside a generic fn — the **#141** shape, where the constraint truth is still a type variable (`EvForward`-exempt by design); (c) the `++`/`mconcat` **lowering-discard** (resolved dict correct but dropped in IR emission — a *lowering* bug a post-resolve pass structurally cannot see). (c) motivates **PHASE 2b (pending): the IR-level dict-passing check** (retro's literal "typed-IR verifier" framing — correlate the threaded dict *argument* in the lowered IR against the constraint's resolved head; `ast_to_ir.translate_append_operands` is the historical `++` discard site). **PHASE 2a DONE 2026-07-13:** return-type dispatch is now IN scope — `verify_call`/`build_theta_ret` also match the callee's declared return `TypeExpr` against the concrete call-site return type (the `TCall` type), binding a return-position constraint var and verifying its injected dict like any other. Validated by a stage-1-vs-stage-2 stats diff on `test_constrained_fn_return_type_nested_tapp.spr` (`verified` 0→1, `mismatched=0`) plus the whole compiler source recompiling clean under the fatal check (`refresh-seed` fixed point at iteration 2). **Residual gap:** class-method return-type dispatch via `TMethodRef` is still uncovered (the sig table is `TFnDecl`-based; `check_call` keys `TVar` callees). Test `test_verify_dispatch.spr` extended: a wrong-headed return-dispatch dict (`Read Bool` vs ret `Maybe Int`) is now `VerifyMismatch`; a correct one (`Read Int`) verifies clean. Note: verifier coverage of the compiler's own source is thin (`verified=6` vs 60/file for dispatch-heavy tests) — most compiler-internal dispatch is polymorphic/forwarded, hence skipped; expected given the scope.
- [x] `P1` **(item 2) Reusable dispatch trace — DONE 2026-07-13, as `SPROUT_TRACE_DISPATCH`.** Env var (not a `--flag`; mirrors `SPROUT_TIME_PHASES`/`SPROUT_GC_*`, no `compile_driver` plumbing), gated so string-building is zero-cost when off. Emits one line per constrained call site: `[dispatch] callee=… class=… var=… <prog_var>-><resolved> … path=… -> <class head|UNRESOLVED>`. **Design:** `resolve_obligation` stays a *pure* function returning `(Maybe TypedExpr, path-tag)` — one tag per cascade branch, the two heuristic branches tagged `(guess)`; the emit lives at the single already-`!{IO}` caller `inject_constrained_fn_dicts`, keeping IO out of the pure inference core (observability guard-rail #3; deviates from `guidelines-adherence-report.md`'s "in `resolve_obligation`" — noted there). The projection sort now traces `callee=vec_sort_by … $t677->Tuple2 $t680->Int path=precise-just -> Ord Int`, making the element-vs-key distinction that caused PR #176 self-evident. Gate: `just trace-dispatch-smoke` (in CI regression-smokes), fixture `tests/trace_dispatch/projection_sort.spr`. Doc: [docs/debugging.md §Typeclass dictionary dispatch]. **Scope:** covers the parametric (`C k`) constraint arm of `inject_constrained_fn_dicts`; the nullary `TConst` (class-only) arm is untraced — extend if a nullary-class dispatch bug ever needs it. **Corpus sweep (160 files, 14,465 events)** — path distribution: 49.6% `precise_tyvar->fwd_or_scan`, 41.1% `fwd_for_prog_var`, 9.2% `precise-just`, 0.08% `first_concrete_arg(guess)`, **0% `scan_prog_to_fresh_for_instance(guess)`** (the PR #176 branch — dead post-fix; see item 3). Pairs with item 1.
- [x] `P2` **(item 3) Make the dict-resolution heuristic loud, not silent. DONE 2026-07-17** (branch `feat/dispatch-loud-precise-miss`). `inject_constrained_fn_dicts` (infer.sprout) now turns a `scan_prog_to_fresh_for_instance(guess)` resolution into a located `InferErr` ("ambiguous typeclass dispatch … refusing to guess") instead of injecting the plausible-but-wrong dict; error raised at the `!{IO}` caller off the pure `resolve_obligation`'s path tag (keeps the resolver pure), gated behind pure exported `dispatch_precise_miss_is_fatal(path)`. `first_concrete_arg(guess)` SPARED (different tag). Escape hatch `SPROUT_DISPATCH_STRICT_OFF` reverts to the legacy guess. **Licensing gate met:** re-ran the sweep on the CURRENT corpus (225 files / 20,863 events) → `scan_prog_to_fresh_for_instance(guess)` still 0×; full `just test` + example canary + smoke/bundle/stress green with the error ACTIVE (0 compile failures, 0 errors emitted). Test = unit tripwire `tests/stdlib/compiler/test_dispatch_precise_miss.spr` pinning the fatal-vs-spared classification (NOT via `test_resolve_evidence`/`stdlib.compiler.resolve` as this item speculated — that's the evidence-resolution PASS; the guess lives in infer's inference-time injection). **FINDING (advisor-confirmed): the guess branch is corpus-DEAD, so no source-level RED exists — this is an invariant-assertion net, not a live-hole fix; its value is realized at Stage 3 (E1), where a reintroduced name-mismatch would revive the branch and now fail loudly.** Item 1 phase-2b (IR-level dict-passing check) NOT bundled — separate/optional.  Original spec follows: Interim safety net short of item 4. The defect *enabler* is `scan_prog_to_fresh_for_instance` (infer.sprout): an order-dependent heuristic that, when the precise `dict_get(prog_var, prog_to_fresh)` lookup misses, silently picks the first `prog_to_fresh` entry with an instance — a plausible-but-wrong dict. When resolution falls through to this fallback for a constraint that *should* have resolved precisely (the prog_var is a known scheme var but missed by name), emit a diagnostic — or a hard error, mirroring the existing `__unresolved_` dict sentinel (`project_typed_codegen_unresolved_dict_nullfill`). Precise-or-loud beats precise-or-guess. Care: distinguish "genuinely still-polymorphic, forward it" (the #141 path — legitimate) from "should have been precise but the name missed" (the soundness hole). The post-2026-07-13 fix should make the latter unreachable; this item asserts that invariant loudly so a regression fails a test instead of a user's program. **Evidence from the item-2 sweep (2026-07-13, 160 files/14,465 dispatch events): `scan_prog_to_fresh_for_instance(guess)` fires ZERO times** — the exact PR #176 branch is corpus-clean post-`canonicalize_constrained_markers`. **Caveat (do not overread): corpus-clean ≠ unreachable ≠ safe-to-blanket-hard-error.** `resolve_obligation`'s header names paths where `prog_var` legitimately isn't in `prog_to_fresh` (the check2 path, non-VarExpr callee) that can still reach this branch; if one currently returns a *correct* guess, hard-erroring it breaks working code. So a hard error needs a source-corpus regression pass (full `just test` + example canary) before landing, not just the sweep. The other surviving guess is `first_concrete_arg(guess)` (12 events, all `Functor`/`Foldable` on `Vec`/`List` where the constraint var is *already the concrete constructor* — `var=Vec -> Functor Vec`), which resolves *correctly*: the "genuinely concrete, not a hole" case to spare. Direction: make `scan_prog_to_fresh_for_instance`'s precise-miss path loud, sparing the concrete-constructor `first_concrete_arg` case. **Testable as a unit tripwire** (not just from source, which the fix closed): `tests/stdlib/compiler/test_resolve_evidence.spr` imports `stdlib.compiler.resolve` and calls resolution on hand-built `TDict` nodes — craft the guess-triggering state and assert it errors. Use `SPROUT_TRACE_DISPATCH` to re-audit the distribution before and after. **Sequence after item 1** (its verifier makes the invariant testable-by-construction and de-risks the hard error).
- [x] `P2` **(item 4) Canonical type-variable identity (architectural root fix) — DONE 2026-07-19: soundness closed earlier (positional, not global-Int-id); §13.3(B) consolidation completed to the agreed scope (@constrained retired; @fwd/@eta_fwd/@super intentionally left).** The durable fix for the whole class; north star in `project_typevar_identity_generalization_gap` / `project_dict_resolution_north_star`. A tyvar is tracked through ≥3 naming worlds — source (`k`), instantiation (`$t478`), post-unification (`$t479`) — reconciled by string-keyed side tables (`prog_to_fresh`, `@fwd`, `@eta_fwd`, `@constrained`). **OUTCOME (corrects this item's original plan):** the global-unique-Int-id approach this item proposed was found **behaviorally INERT** — `generalize` re-derives binders from ftv *names* and `instantiate` re-mints `fresh`, so var identity (name OR id) is destroyed+recreated each round-trip; keying by id misses exactly where name-keying does. The real fix is **POSITIONAL scheme-var identity** (position is stable across generalize/instantiate). Landed: **Stage 3** positional `@constrained` markers (PR #199, `3b4d824`) + **Stage 4** delete the now-dead unsound `scan_prog_to_fresh_for_instance` guess (PR #204, `3be38a1`) + **sub-campaign α** codegen dispatch identity, mangle class-method dispatchers (PR #206, `907b962`, item 5b). **The soundness class is closed:** corpus sweep (229 files/21,254 events) shows the unsound guess fires 0×; `@fwd`/`@eta_fwd`/`prog_to_fresh` are seeded+consumed within one body's instantiation (no round-trip) so their name-keying already hits — positional-izing them would be inert. **§13.3(B) consolidation — DONE 2026-07-19** (branch `feat/scheme-constraints-consolidation`). Gave `Scheme` a `List (head_token, ClassName)` constraint field (S1, iface format v4) and made it the single source of truth for the **`@constrained`** marker family: populated at generalize (S2/S3), at the recursion self-binding, and at the pre-scan forward-ref entry (S4), with BOTH consumers flipped onto it (inference-time injection + the return-position post-pass) and the entire `@constrained` machinery deleted — `register_constrained_fn_markers`/`inject_constrained_fn_dicts`/`build_constrained_fn_dicts` + `canonicalize`'s marker write + `module_loader`'s `@constrained_` key-prefixing (net −100 LOC in compiler source). Byte-identical across the 243-file corpus at every sub-step. **SCOPE DECISION (Kuba, 2026-07-19): the `@fwd`/`@eta_fwd`/`@super` families are intentionally LEFT seeded — NOT collapsed into the field.** Per `retros/design-canonical-identity.md §19.1`, they are body-local (seeded AND consumed within one body's instantiation, no generalize→instantiate round-trip), so their name-keying already hits and deriving them from the Scheme field at lookup would be behaviorally-inert churn on the bootstrap-critical dispatch lookup (the "inert-rep trap"). **§19.2's original S4/S5 (the `@fwd` derive-at-lookup) is WITHDRAWN, not deferred — do not re-attempt it.** Regression guards: `tests/stdlib/test_constrained_recursion_dispatch.spr` (recursion + forward-ref + mixed tyvar/concrete through the field path), `test_mixed_constraint_dispatch.spr` (external-call mixed). **Item 4 is now fully closed** (soundness done earlier; consolidation done here to the agreed scope).
- [x] `P2` **(item 5) Codegen dispatches the Semigroup operator on the SOURCE NAME `append`, hijacking any user function so named — DONE 2026-07-18 via root-cause fix (b), across two commits: resolution side = 5b Part 1 (`69ed549`), codegen side = 5b Part 2 (`cbf03a7`). Acceptance criterion met: a user `fn append` now compiles correctly (regression test `test_classmethod_dispatch_identity.spr`), no stdout-swallowed codegen bail. See the 5b sub-items below.** Found 2026-07-14 building `tests/stdlib/test_task_nested_scope.spr`: a top-level `fn append(log: Ref String, s: String) -> Unit` silently broke codegen for the whole bundle. **Mechanism (verified in source):** the parser desugars `a ++ b` to `CallExpr("append", [a, b])` (`ast_to_ir.sprout:4201`), and codegen routes the append/Semigroup lowering purely on the callee *name string* — `if fname == "append" then translate_append_call(...)` (`ast_to_ir.sprout:4322`). So **any** call to a function literally named `append`, regardless of its actual binding/type, is force-routed into the Semigroup `++` lowering, which expects `String`/`List` operands or a resolved instance-dict witness. A user `append` returning `Unit` with no witness falls through to the `else` at `ast_to_ir.sprout:4307` and bails: `` `++`/append on a non-String/List type with no resolved Semigroup witness``. **Why it's insidious (3 failure-amplifiers):** (a) the error names `++`, code that never wrote `++` — it describes the *desugared* form; (b) the error is written to **stdout** (becomes line 1 of the emitted `.ll`) while `--emit-ir` **exits 0**, so it surfaces only later as an opaque `clang` "expected top-level entity" link error; (c) the trigger is the bare string `"append"`, not scope shadowing — a wholly unrelated function is hijacked. Same root class as items 1–4 in this section: **codegen keying dispatch on a source name instead of the typechecker's already-resolved identity** (cf. the `vec_sort`/`vec_sort_by` name-vs-witness bugs). **Memory:** `feedback_append_name_collides_semigroup` (also flags `map`/`compare`/`mempty` etc. as effectively-reserved names with no protection). **Prior-art survey (verified against primary sources, 2026-07-14):** the collision exists only because Sprout's operator desugars to a plain *identifier* in the value namespace. Comparable languages prevent it structurally, in two camps — (1) *operator target is a member, never a free identifier*: **Rust** `a + b` → `std::ops::Add::add` trait method ([reference](https://doc.rust-lang.org/reference/expressions/operator-expr.html)); **Scala** `a ++ b` ≡ `a.++(b)`, operators are methods ([docs](https://docs.scala-lang.org/tour/operators.html)); **Swift** the operator symbol itself is the function name, `static func +`, so an alphanumeric identifier can never collide ([Swift book, Advanced Operators](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/advancedoperators/)). (2) *same choice as Sprout — class methods share the top-level namespace — but with a hard conflict rule*: **Haskell 2010 Report §4.3.1**: "Class methods share the top level namespace with variable bindings and field names; they must not conflict with other top level bindings in scope. That is, a class method can not have the same name as a top level definition …" ([report](https://www.haskell.org/onlinereport/haskell2010/haskellch4.html)). So top-level class-method names are a *legitimate* design (Haskell's) — the gap is the two missing safeguards. **Two fixes (analyze which, or both):** (a) **cheap/defensive (Haskell's rule):** at check time, reject a user `fn` whose name equals a class method (or any operator-desugar target) as a duplicate-definition error — turns the silent miscompile into a located diagnostic; small, but leaves the codegen name-magic. (b) **root-cause:** stop dispatching codegen on the string `"append"`; the typechecker has already resolved the call to a specific `Semigroup` instance (or a user function) — key `translate_append_call` off that resolved marker/identity, not the source name. Aligns with item 4 (canonical identity) and the general "resolve, don't re-match names" direction. **Acceptance criterion:** a program with a user-defined `fn append` (or `map`/`compare`) either compiles correctly (root-cause fix) or is rejected at compile time with a clear located diagnostic that names the collision (defensive fix) — never a stdout-swallowed codegen bail masquerading as a link error; regression test wired into a gate. **Sequence:** independent of items 1–4; the defensive fix (a) is landable now as a quick guard, the root-cause fix (b) is best folded into item 4's identity work.
  - [x] **(item 5b — value-namespace fix) DONE 2026-07-17** (branch `feat/canonical-tyvar-identity`). Root-cause fix, not the defensive guard: the parser no longer desugars `a ++ b` to a bare `CallExpr(VarExpr("append"))` — it emits `ast.BinaryExpr("++")` (symmetric with `+`/`-`), and `infer.infer_binary_op` gains a `"++"` case (`append_via_semigroup`) that dispatches to the Semigroup class method by IDENTITY, selecting the instance via `@inst:Semigroup:{head}` directly — a key no user `fn append` can clobber (the old path went through the evictable `@class:append` marker / value-namespace name). Emits the same `TCall(append, [TDict, l, r])` node codegen already lowered, so `ast_to_ir` is untouched. A user `fn append` is now an ordinary function; `++` is immune. NOTE: chose (b)-style root fix over (a) the defensive guard because the guard would REGRESS `stdlib.bytes` (legit qualified exports `empty`/`append`/`to_string`). Regression test `tests/stdlib/test_operator_not_hijacked_by_user_fn.spr` (RED = check-phase `Ref String vs String`; GREEN = `++` = Semigroup). Full compiler-source gate chain green + seed refreshed (fixed point iter 2).
  - [x] **(item 5b — Part 2: codegen symbol collision + dispatch hijack) DONE 2026-07-18** (commit `cbf03a7`, branch `feat/dispatch-identity-followups`, canonical-identity sub-campaign α). Fixed BOTH remaining codegen name-as-identity manifestations: (1) the SYMBOL COLLISION — every class method emitted a bare dispatcher symbol (`@append`, `@eq`, `@to_string`, `@compare`, `@empty`, `@fmap`, `@fold_values`), colliding with an emitted user `fn <class-method-name>` → `clang: invalid redefinition`; (2) the DISPATCH HIJACK — `ast_to_ir`'s `++` peephole keyed on `fname == "append"`, so a user `fn append` emitted `ast_to_ir: append expects at least 2 arguments` (1-arg) or a silent `str_concat` miscompile (2-string). **Fix:** a shared `classmethod_dispatch_name(class, method)` (lowering) mangles every dispatcher to `__cm_<Class>_<method>`, applied at BOTH the wrapper DEFINE (`generate_one_class_wrapper`) and every dispatch CALL site (`lower_dispatch_callee`, at the `TCall` lowering choke point). The rewrite is gated on the infer-injected leading `TDict` witness — present for every genuine dispatch (input/return/forward position), absent for a user shadow — so a shadowing top-level fn keeps its bare name and calls the user's own function. `ast_to_ir`'s `++` peephole re-keys on `"__cm_Semigroup_append"`. Behavior-preserving (every dispatch already routed through the bare wrapper; this is a consistent rename — value-position eta still targets `@__tc_`). Regression test `tests/stdlib/test_classmethod_dispatch_identity.spr` (user `append`/`to_string` shadow + genuine String/List `++`). Self-hosts to a fixed point; full gate chain green + seed refreshed. This closes the codegen-side root-cause fix (b) for item 5 above.
- [x] `P1` **(type-system review 2026-08-13) A compound-head constraint `where C (T a)` resolves
  its dictionary by grabbing the first concrete argument (`infer.sprout:2033`, canonicalization at
  `:5773-5776`). FIXED 2026-08-13** — the head is now preserved, as the "Fix" paragraph below
  asks. `canonicalize_constrained_constraints_acc` (and `constraint_source_tokens`, so a
  self-recursive call behaves like an external one) emits `"#app:<HeadCtor>"` for a `TypeApply`
  head instead of `"#none"`, and `resolve_arg_scanned_tdict` picks the argument whose resolved
  type is headed by that constructor rather than whichever is concrete first. When no argument
  matches the head the obligation is **forwarded** (`resolve_via_fwd_or_scan`) rather than filled
  from an unrelated type — filling it was the defect. A genuinely headless constraint keeps the
  old positional scan unchanged.
  Two sites had to move together, and the second is the trap: `build_constrained_fn_dicts_via_field`
  branched on `head_token == "#none"` to mean "argument-scanned, emit nothing here", so a
  `"#app:"` token would have fallen into the *return-type* scan and injected `@inst:ToString:String`
  for a `where ToString (Box a)` function returning `String`. Both sites now share
  `is_arg_scanned_token`. The token is the SOURCE head name while resolved types carry the
  module-qualified one, so matching is on the final dotted segment (`head_name_matches`).
  `iface_codec`'s wire form needed no change — `"#app:Box"` is atom-safe exactly as `"#pos:0"`
  already was — but its comment now lists the form.
  Verified: `describe_second(n: Int, b: Box a) -> String where ToString (Box a)` printed
  `35184372089336`, a raw pointer read through `Int`'s dictionary, and now prints the box.
  `tests/stdlib/test_compound_constraint_head_order.spr` was RED first at exactly 2 of 5 checks —
  the other three are guards that already passed and must keep passing, including a
  class-polymorphic caller (`shout_boxed` → `shout … where ToString a`), since concrete instances
  devirtualize and would not exercise dict forwarding at all. The sibling
  `test_compound_constraint_inline_record.spr` still passes. Compiler self-hosts (stage-3), sweep
  of 414 files unchanged, full suite green. Still open from the original entry: routing this arm
  through `trace_dispatch` so it stops being invisible to corpus sweeps. Original analysis follows.
  `canonicalize_constrained_constraints_acc` **discards** a type-application
  constraint head entirely, emitting the token `"#none"`.
  `inject_constrained_fn_dicts_via_field`'s `#none` arm then resolves the dict by scanning the
  call's arguments left to right for the first one with a concrete type head
  (`first_concrete_typed_arg_str_excluding`, `:1316-1330`), committing with **no check that the
  argument's type relates to the constraint's head and no backtracking**. Two failure modes, one
  root cause: (a) if an earlier unrelated argument has an instance of that same class, its dict
  hijacks the slot — a silent miscompile ranging from wrong output to a `non-exhaustive match`
  abort to a SIGSEGV inside `str_concat`; (b) if that first concrete-headed argument has *no*
  instance, the scan gives up rather than continuing to the argument that does match, and the
  user gets a leaked internal string ("internal error: under-application of … reached codegen").
  Reproduced: `fn describe(tag: Int, b: Box a) -> String where Sh (Box a) = sh(b)` prints
  `int(35184372088840)` while the identical body with the parameters swapped prints `box(int(1))`
  — same constraint, different dictionary, chosen only by parameter order. **This is NOT the
  `first_concrete_arg(guess)` heuristic that item 3 above audited and deliberately spared.** That
  tag is produced at `:1949` inside `resolve_obligation`, which is also the only path
  `SPROUT_TRACE_DISPATCH` instruments; the `#none` arm returns before any trace call, and a
  miscompiling call site emits **zero** dispatch events. The corpus sweeps that licensed sparing
  the heuristic (160 files/14,465 events, later 225/20,863) were structurally incapable of
  observing this path. Sibling of — not a duplicate of — the stale-subst `#none` fix below: that
  one corrected `subst`→`s3` within the arg scan; this is the arg scan existing at all.
  The shape is supported and regression-tested
  (`tests/stdlib/test_compound_constraint_inline_record.spr:16`), but existing coverage only ever
  passes the constrained value as the **first** argument — precisely where the positional guess
  accidentally agrees with the truth. **Fix:** do not guess, the head is known —
  `unifier.apply_subst(s3, fresh_t)` on the constraint head yields `Box Int` at the call site, so
  resolve `@inst:C:<head-ctor>` directly (recursing to discharge the instance's own premise)
  exactly as the concrete-ctor path at `:5769-5772` already does. That means preserving a real
  token for a `TypeApp` head in `canonicalize_constrained_constraints_acc` instead of collapsing
  to `"#none"`. If an argument scan is retained as a fallback it must unify each candidate's
  substituted type against the constraint's substituted head before committing, and keep scanning
  past an `@inst` miss. Failing either, raise the located "ambiguous typeclass dispatch … refusing
  to guess" error item 3 already built — precise-or-loud, never precise-or-guess. Whichever route,
  route the `#none` arm through `trace_dispatch` so it stops being invisible to corpus sweeps.
- [x] `P1` **(type-system review 2026-08-13) An ambiguous class type variable is never reported:
  undefined link symbol, or a caller-dependent instance (`infer.sprout:1191-1193`, `:1772-1790`).**
  The classic `show . read` ambiguity — here `to_str(from_int(n))`, where the dispatch tyvar is
  determined by nothing — is not diagnosed. `find_fwd_tdict_in_args` (`:1237`) misses because the
  fresh tyvar has no `@fwd:{tvar}:{class}` marker, and `scan_fwd_markers` (`:1775`) then does a
  **class-only** scan of the whole env. Two outcomes: (a) with no `@fwd:*:{class}` marker in
  scope, `check_instance_fwd`'s final fallback returns the call unchanged with no dictionary and
  no error — `--phase check` and `--emit-ir` both exit 0, resolve and `verify_dispatch` both pass
  clean (`verify_call` keys off the *callee's declared `where` clause*, and a class method has
  none), and the sole user-visible symptom is `clang: use of undefined value '@from_int'` with no
  Sprout source position. (b) with any such marker in scope, `scan_fwd_marker_entries`
  (`:1786-1790`) returns the **first** one in `dict_entries(env)` order, so the ambiguous
  expression silently adopts a dictionary belonging to an unrelated type variable. Reproduced:
  in `fn amb(x: b, n: Int) -> String where Conv b = to_str(x) ++ "/" ++ to_str(from_int(n))`, the
  closed subexpression `to_str(from_int(7))` — no free variables, no connection to `x` — prints
  `bool` in one call and `int:7` in another. **That is incoherence, not merely a missing
  diagnostic.** Two of the code's own asserted invariants are falsified: `:1189-1190` claims
  "only a rigid function head reaches here unresolved" (case (a) reaches it with an ambiguous
  fresh tyvar), and `scan_fwd_markers`' header describes itself as the fallback for a constraint
  var "not visible at the head of any argument (e.g. `Ord k` where `k` appears inside a tuple)"
  — i.e. it assumes the obligation belongs to a real declared constraint var that is merely
  syntactically hidden, and has no way to tell that case from case (b). Case (a) is exactly what
  `docs/spec-v0.md:1224-1226` says the constraint well-formedness rules exist to prevent, but
  those rules only inspect declared `where` variables, so a body-local ambiguity with no `where`
  clause at all slips past. **Fix:** do not delete `scan_fwd_markers` — its stated role is
  legitimate — but gate it: before returning a marker, require the unresolved dispatch tyvar to
  correspond to that marker's prog var (check it occurs in the enclosing declaration's constraint
  set / `prog_to_fresh` image rather than being fresh from the call itself). When the dispatch
  tyvar is tied to no declared constraint and no concrete type, emit a located "ambiguous type
  variable: cannot determine which `C` instance to use" (the Haskell-style ambiguity check). That
  also fixes (a), since the silent unchanged-call fallback becomes unreachable for a tyvar-headed
  dispatch and can then be tightened to "reject unless the head is a rigid non-instantiable type".
  **Measured 2026-08-13 — do NOT simply reject at the fallback.** Both halves were re-reproduced
  against the current compiler: (a) still reaches `clang: use of undefined value '@from_int'` with
  no source position, and (b) still prints `int:7` then `bool` for the SAME closed subexpression
  `to_str(from_int(7))`, decided by an unrelated argument. Then the cheap version of the fix —
  turning `check_instance_fwd`'s dict-less fallback into a located error — was built and swept:
  it **over-rejects 8 existing test files**, all of them return-position dispatch
  (`test_return_type_dispatch`, `test_constrained_fn_return_type_dispatch`, `test_applicative`,
  `test_deriving_enum`, `test_devirt_classmethods`, `test_typeclass_laws`,
  `test_classmethod_dispatch_identity`, `test_type_identity_dispatch_paths`). The experiment was
  reverted, unlanded. **This reframes the fix:** that fallback is not a catch-all for ambiguity,
  it is the deliberate hand-off to the post-pass `resolve_dispatch_typed_expr`, which fills the
  TDict once outer unification makes `ret_t` concrete. A return-position dispatch legitimately has
  no dictionary *yet*. The ambiguity signal is therefore "still has no dictionary AFTER the
  post-pass has run", so the check belongs in/after that pass (or in `verify_dispatch`, extended
  to require a class-method call to carry a dict — today `verify_call` keys off the callee's
  declared `where` clause and a class method has none, which is exactly why this slips through).
  The compiler's own source self-hosts under the strict version (stage-3 clean), so the affected
  surface is genuinely user-facing return-type dispatch, not compiler internals.
  **FIXED 2026-08-14 on branch `fix/ambiguous-class-tyvar`.** Both fixtures
  (`tests/conformance/type_error/ambiguous_class_tyvar{,_incoherent}.spr`) now fail with a located
  `ambiguous type variable in <method>: nothing determines which <Class> instance to use`. Spec
  rule written up in `docs/spec-v0.md` §Constraint syntax ("Ambiguous class-method dispatch").
  The landed design is a **gate plus a repair**, which is NOT the gate-only shape originally
  planned here — the difference is the important part for anyone touching this code:
  - **Part 2 — the loud check (`verify_dispatch.sprout`).** Class decls survive inference as
    `TPassThrough (ast.ClassDecl …)` carrying their method signatures, so the verifier now builds
    a class-method signature table alongside its `TFnDecl` one and treats each method as a callee
    whose sole constraint is its class applied to its own type parameter. The new rule is scoped
    to methods (`MissingPolicy`): for a constrained `fn`, "no dict injected" is DEFERRED (a
    legitimate forward through a hidden dict param), but a class method has no `where` clause to
    forward through and no later pass to fill it in, so absent is FINAL. This is why the earlier
    "reject at `check_instance_fwd`'s fallback" experiment over-rejected 8 files and this does
    not: by the time `verify_program` runs, both dispatch post-passes have already had their turn.
  - **Part 1 — the gate (`infer.sprout: gated_fwd_scan`).** The class-only `scan_fwd_markers`
    fallback is now only reached when the caller has no dispatch type in hand (the
    constraint-var-hidden-in-a-tuple case it was written for). With a dispatch type, only that
    variable's own `@fwd` marker is adopted.
  - **The gate ALONE over-rejects, and this is the load-bearing discovery.** At inference time a
    legitimate forward and an ambiguous call are *indistinguishable*: in `mconcat`'s
    `fold(\ (acc, x) -> acc ++ x, empty(), xs)`, `empty()`'s fresh return variable has not yet
    been unified with `Monoid a`'s variable, so it looks exactly like the ambiguous
    `from_int(7)`. Only the declaration's FINAL substitution separates them. So the adoption
    decision moved into the post-pass: `resolve_dispatch_typed_expr` already receives `fwd_env`
    plus the final `s2`, and now repairs a still-polymorphic return-position dispatch by finding
    the marker whose variable the substitution has identified with the dispatch variable
    (`forwarded_tdict_for_tyvar`). Anything left dict-less after that is genuinely ambiguous.
  - **The marker match is equivalence-under-substitution, not a key lookup.** Unification may
    orient a binding either way, so the marker's key can name the variable that was substituted
    away; both sides are substituted before comparing. A raw identity comparison reintroduces the
    mis-forwarding class of PR #176/#141. The direct key hit is a fast path only.
  - **Sweep result:** 514 corpus files (`tests/stdlib`, `tests/conformance`, `examples`) checked
    with `SPROUT_VERIFY_DISPATCH_OFF=1`; exactly the two intended fixtures report. Two
    false-positive classes were found and fixed on the way: `$`-prefixed heads (`$sk<n>` skolems
    and `$ex_<var>` existential forwarding identities) were being compared as if they were type
    constructors, and a top-level `fn` shadowing a class-method name (`test_classmethod_dispatch_
    identity`'s `fn append`) left the method's signature registered.
- [x] `P2` **`++` (Semigroup) dispatch resolved by an unrelated marker — FIXED 2026-08-14, same
  branch.** Initially deferred, then closed once a repro showed the hole was not merely an
  ambiguity gap but a **live silent miscompile**. `find_fwd_tdict_in_args` reads each operand's
  RAW node type, so a lambda parameter — whose type is a fresh variable until the lambda unifies
  with the higher-order function's parameter — misses its own marker and `++` fell through to the
  class-only scan. With TWO `Semigroup` constraints in scope that scan returned whichever marker
  came first in dict order, so in
  `fn fold_both(xs: List a, ys: List b, …) where Semigroup a, Semigroup b` the `List` fold ran
  with `String`'s dictionary: `__tc_Semigroup_String_append` applied to a list, printing a tab and
  two control bytes instead of `[9, 1, 2]`. Verified present on master `257f7638` too, so it was
  pre-existing rather than introduced by the gate. Fix = `maybe_forward_input_dispatch`, the
  symmetric input-position repair (a dict-less input-position call whose dispatch type is still a
  variable gets the marker its variable is identified with under the final substitution), which
  is what makes passing `Just(t)` at the `++` site safe. Regression test:
  `tests/stdlib/test_semigroup_append_dispatch_identity.spr` (both constraint orders, plus the
  single-marker `mconcat` shape and direct non-lambda operands as controls).
- [ ] `P2` **The remaining ungated scan (`class_var_arg_or_fallback` found no class-var arg) can
  still pick the wrong marker.** Discovered while testing the `++` fix. When
  `check_instance_for_marker` cannot identify which argument carries the class variable, it calls
  `check_instance_fwd` with `Nothing`, which keeps the ungated class-only scan — and that scan has
  the same first-in-dict-order defect. Repro: in the `fold_both` shape above, add
  `ToString a, ToString b` and render with `to_string` instead of passed-in functions; BOTH
  `to_string` calls lower to `__cm_ToString_to_string(…, %__tc_ToString_2_to_string)` — the dict
  for `a` — so the `b` value is rendered through `a`'s instance. (`SPROUT_TRACE_DISPATCH=1`
  confirms the CALLER resolves all four dicts correctly; the mis-selection is inside the callee.)
  Not fixed here because the obvious fix does not work: making that site gated as well (declining
  to scan) breaks the prelude's `map4` with `No instance of Applicative for a function type`, so
  the scan is load-bearing for at least the Applicative shape. Closing this needs the post-pass
  repair to cover the no-class-var-arg case — `dispatch_type_for_vars` already searches the
  declared parameter types structurally, so it can find a class variable nested inside a
  container, which `class_var_arg_or_fallback` cannot — and then the Applicative path re-checked
  against it.
- [ ] `P3` **`extern fn str_slice(s: String, from: Int, to: Int)` misnames its third parameter.**
  The third argument is a **length**, not an end index — the runtime signature is
  `str_slice(long long s, long long start, long long length)` and `prelude.sprout` documents it
  inline, but the `extern fn` declaration itself says `to`. Reading the declaration rather than
  the comment produces a slice that is wrong by exactly `start` characters, which is silent
  whenever the caller then compares the result against something (the marker-key parser in the
  ambiguous-class-tyvar fix lost a session to it). Rename the parameter to `len`. Note this is a
  `stdlib/prelude.sprout` edit, so it needs its own reseed cycle even though no IR changes.
- [ ] `P2` **Pattern-variable names share the fresh-tyvar namespace (`infer.sprout:2050`)** (fundamentals review, static finding, not yet exercised at runtime). Pattern-bound variable names and the inferencer's fresh `t0`/`t1`/… type-variable names are drawn from the same namespace with no collision guard; a match-pattern binding whose name happens to collide with a fresh tyvar could shadow, or be shadowed by, the wrong entity during unification/substitution. Not yet triggered by a known repro — flagged as a latent hazard by the 2026-07-03 fundamentals code review among the "high/static (not yet run)" findings, adjacent to this section's tyvar-identity work (item 4). Needs a minimal repro to confirm reachability, then either a namespace separator (reserve a prefix for fresh tyvars, distinct from any user-writable pattern-variable name) or a rename pass before pattern binding. Full findings: `docs/fundamentals-code-review-handoff-2026-07-03.md`.

#### Record type-arg concretization at dispatch (surfaced by deriving-on-records)

> Root pattern: an ADT constructor-application node is born concretely typed (`Box Int`), but a record-construction node carries `Box $a` with the `$a -> Int` binding living only in the substitution. Any dispatch/resolution site that reads the raw node type instead of applying the resolved substitution silently works for ADTs and breaks for records (unresolved-dict / poison-thunk crash). Deriving-on-records (PR #9) flushed out three such sites; two were fixed there, the third here.

- [x] `P1` **Stale pre-argument subst in the `#none` (compound-head) constrained-fn dispatch branch — DONE (branch `fix/none-branch-stale-subst`).** The third instance of the pattern above. `inject_constrained_fn_dicts_via_field`'s `#none` branch (`infer.sprout` ~1901) resolved a headless/compound-head constraint (`where ToString (Box a)`, whose head is a type application, not a bare var — see `canonicalize_constrained_constraints` ~5087) by scanning the args with the **stale pre-argument `subst`** instead of `s3`. An inline parametric-record argument carries its `a -> Int` binding only in `s3`, so the stale subst left it `Box $free` → poisoned dict → unresolved-thunk runtime crash. Fixed `subst`→`s3` (mirrors the `check_instance_for_marker` fix in the deriving-records PR; its sibling `resolve_field_constraint` path already used `s3`). Confirmed LIVE by repro (RED) then GREEN: regression test `tests/stdlib/test_compound_constraint_inline_record.spr` (`describe(b: Box a) where ToString (Box a)` + `eq_boxes … where Eq (Box a)`, called with inline records).
- [ ] `P2` **Wire in the dead `assert_resolved_typed_expr` soundness pass (shift dispatch bugs left to compile time).** `infer.sprout` ~4830 has a pass that flags free TVars in the final typed AST, but it is **never called**. INVESTIGATE first whether it actually catches this class: the record dispatch bugs poisoned the *injected dict evidence* while the final node type may already be concretized (`Box Int`) after the declaration-boundary `apply_subst` — so a node-type-only check may miss them. If confirmed (or extended to also check each injected `TDict`'s constraint-head against its resolved arg type — overlaps with `verify_dispatch` item 1 above), wire it behind a debug/CI flag so this class fails at compile time rather than the runtime unresolved-thunk backstop.
- [ ] `P3` **Compiler-internals hazard note: read the resolved type, never the raw node type, in dispatch/resolution.** Add a short note to `docs/compiler-internals.md` documenting the ADT-vs-record concretization asymmetry above and the invariant "resolve constraint/dispatch arg types via `apply_subst(s3, …)`, not `typed_expr_type` alone." Three sites hit this; a written invariant stops the next one at review time.

