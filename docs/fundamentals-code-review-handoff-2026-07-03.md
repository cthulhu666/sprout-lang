# Fundamentals Code Review — Fix Handoff (2026-07-03)

**Status:** findings verified, fixes NOT started. This doc is the work plan for follow-up
sessions. Each workstream is sized for one session unless noted, and is written so an
agent can execute it without re-deriving the analysis.

**Origin:** adversarial review of `runtime/sprout_runtime.c`, both codegen paths
(`codegen.sprout` direct, `ast_to_ir.sprout`/`ir_lowering.sprout` typed), the type system
(`infer.sprout`, `resolve.sprout`, `checker.sprout`, `unifier.sprout`), and
`prelude.sprout`/`lexer.sprout`/`parser.sprout`. Every CRITICAL finding below was either
reproduced end-to-end (probe programs, Appendix A) or verified at source level.
Refuted candidates are listed in Appendix B — do not re-investigate them.

**Headline:** each core language promise currently has at least one confirmed hole —
effects are not enforced at all (F-EFF), declared type signatures are not checked against
bodies (F-RIGID), top-level heap globals are swept by the GC on the typed path (F-GROOT),
strings admit out-of-bounds heap reads (F-UTF8), and `/` is LLVM UB on zero (F-DIV).

---

## 1. Reproduction harness

```sh
# Type-check verdict (exit 0 + env dump on acceptance):
./build/compile_driver_bin_stage1 --phase check stdlib <probe.sprout>

# Full run (macOS needs the frameworks):
./build/compile_driver_bin_stage1 --emit-ir stdlib <probe.sprout> > /tmp/p.ll
clang /tmp/p.ll runtime/sprout_runtime.c -framework Security -framework CoreFoundation -o /tmp/p
SPROUT_GC_STRESS=1 /tmp/p   # stress oracle for rooting bugs
```

Probe sources are inlined in Appendix A (originals were in `/tmp/sr_*.sprout`; treat the
appendix as canonical). **First step of every workstream: copy its probes into
`tests/stdlib/` / `tests/conformance/` as regression tests (DoR), confirm they fail for
the right reason, then fix.** For rejection tests (program must NOT type-check), find and
follow the existing negative-test convention in `tests/` before inventing one; if none
exists, that is a small test-harness sub-task to do first.

## 2. Decisions required from Kuba (blocking the marked workstreams)

Per AGENTS.md §Collaboration 5/6 these need sign-off before implementation. Everything
else in this doc is a clear single-solution bug fix and can proceed without asking.

- **D1 (blocks W7):** semantics of `Int` division: (a) runtime panic with location on
  `x/0` and `INT_MIN / -1` — matches the "runtime errors minimized but explicit" stance;
  (b) `/` returns `Result`/`Maybe` — matches guidelines §2 totality but breaks all code.
  Also: pick and document `Int` overflow semantics (current native behavior is two's-
  complement wrap; interpreter is arbitrary-precision — divergent).
- **D2 (blocks W6 rollout shape):** effect enforcement will reject currently-compiling
  code. Proposed rollout: land checks behind `SPROUT_EFFECTS_ENFORCE=1`, fix all stdlib/
  examples/compiler violations, then flip the default and delete the flag. Approve or
  choose direct flip.
- **D3 (blocks W10):** is the direct codegen path (`--use-direct-codegen`, codegen.sprout)
  being retired after the typed flip completes? If yes, W10's direct-path-only items
  (C2, C3, C4, C5b, C6) become "add loud panics + retire" instead of full fixes.
- **D4 (blocks part of W2):** ingestion policy for invalid UTF-8 from external sources
  (`proc_run` stdout, `tcp_read`, `term_read_line`, `env_get`, `argv_get`): reject via
  the builtin's error channel where one exists, vs lossy-replace with U+FFFD. Note some
  of these return bare `String` today, so "reject" may force signature changes.
- **D5 (blocks P1/P2 in W8):** breaking signature changes: `split_ints : String -> List Int`
  (returns 0 for junk words) and `mutvec_get : MutVec a -> Int -> a !{IO}` (no bounds
  check, C UB). Proposed: `Result`-/`Maybe`-returning replacements.

## 3. Workstreams (recommended order)

Sizing: S = one focused session. Delegation guidance per project memory: W2/W8/W9 are
mechanical enough for Sonnet subagents; W1/W3/W4/W6 touch GC or inference internals —
keep for a strong model with the docs below read first.

Required reading before any compiler/runtime session: `docs/compiler-internals.md`,
`docs/debugging.md`. Process gates for EVERY session are in §4.

---

### W1 — F-GROOT: typed path never GC-roots top-level `let` globals  [1S, CRITICAL, confirmed empirically]

- **Evidence:** probe `sr_globalroot` prints `x` instead of `hello, world` under
  `SPROUT_GC_STRESS=1` (silent use-after-free), correct output without stress.
- **Locations:** `stdlib/compiler/ast_to_ir.sprout:4667-4695` (`synthesize_init_body_loop`
  emits only `IRStoreGlobal`); `stdlib/compiler/ir_lowering.sprout:122` (IRStoreGlobal
  lowering — no registration; `ir_header()` doesn't even declare
  `@sprout_gc_register_i64_root`); contrast direct path
  `codegen.sprout:3291-3320` (`emit_global_root_registration`).
- **Why unnoticed:** neither stdlib nor the compiler uses top-level `let`; only user
  programs are exposed.
- **Fix sketch:** add an explicit `IRRegisterGlobalRoot(name)` op to `sprout_ir.sprout`
  (the exhaustive matches in `ir_rooting.sprout` — `op_triggers_gc` /
  `op_produces_simple_heap` / `op_exposes_operands` — will then FORCE classification at
  compile time, which is the point of that design). Emit it once per global after the
  init store in `synthesize_init_body_loop`; lower to
  `call void @sprout_gc_register_i64_root(ptr @<name>)`; add the declare to `ir_header()`.
  Scalars may skip registration only if `eval_const_expr_ir` proves Int/Bool/Unit —
  when in doubt, register (registering a scalar slot is harmless with the i64-root API).
  Update the `IRLoadGlobal` comment in `ir_rooting.sprout:176` whose safety argument
  ("global slot is a permanent root") this fix makes true.
- **Regression tests:** `sr_globalroot` as a `.spr` test run under `SPROUT_GC_STRESS=1`
  (see `just test-stress` wiring); assert exact output. Add a variant where the global
  is a `List String` (ctor, not string concat).
- **Gates:** compiler-source change → smoke shapes, bundle smoke, `just refresh-seed`
  BEFORE `just test` (see §4), examples canary.

### W2 — F-UTF8: runtime string-safety batch  [1S, CRITICAL, source-verified; partially blocked on D4]

All in `runtime/sprout_runtime.c`. No compiler changes, no seed refresh. No new builtins
(APPROVED_BUILTINS untouched).

- **R1 — OOB walkers (unblocked, do first):** `sprout_utf8_char_width` (:3625) returns
  2/3/4 with no bounds check; callers `sprout_utf8_codepoint_count` (:3648),
  `sprout_utf8_byte_offset` (:3658), `str_char_at` (:3748), `str_char_at_unboxed`
  (:3922), `str_find` (:4019), `str_slice` add width blindly → heap OOB read when a
  string ends in a truncated multibyte sequence. Fix: before advancing, verify bytes
  `i+1 .. i+width-1` are present (non-NUL) and are continuation bytes; on violation,
  clean `tcp_fail`-style panic (interior code may assume validity only once ingestion
  validates — until W2 completes fully, the walkers are the last line of defense; keep
  the check permanently anyway, it's O(1)).
- **R2 — unvalidated ingestion (needs D4):** `utf8_validate` (correct, :6276) is called
  only from `read_file` (:1581) and one other site (:6332). Missing at: `proc_run`
  stdout/stderr (`sprout_make_proc_result` :1660), `tcp_read` (:7025, also silently
  truncates at embedded NUL), `term_read_line` (:1820) + `_unboxed` (:3905), `env_get`
  (:1485) + `_unboxed`, `argv_get` (:1511) + `_unboxed`, `stdin_read_bytes` (:1840).
  The runtime's own contract comment (:313-314) claims these validate. Apply D4's policy
  at each site.
- **R3 — `char_to_str` / `char_from_codepoint` (unblocked):** (:4168 / :4206) accept
  negative, >0x10FFFF, and surrogate codepoints, minting invalid Strings from pure Sprout
  code (`char_to_str(-1)`). Fix: validate range (0..0x10FFFF excluding 0xD800-0xDFFF);
  reject path per D4/D1 style (a clean panic is acceptable here pending a `Maybe` design
  at the prelude layer).
- **R4 — `term_read_key` (:1883, minor):** returns a pointer to a mutable `static char
  buf[2]` — a retained String silently mutates on the next call; also unvalidated raw
  byte (feeds R1). Fix: heap-allocate + register like every other string builtin,
  validate the byte.
- **Regression tests:** `.spr` tests: `proc_run` capturing `head -c 3 /dev/urandom`-like
  truncated output then `string.length`; `char_to_str(-1)`; `char_to_str(1114112)`.
  Wrap the OOB repro so it fails loudly pre-fix (ASan one-off is acceptable evidence
  during DoR: `clang -fsanitize=address`).
- **Gates:** runtime change → example canary (run, not just compile): `tuples`,
  `factorial`, `maybe_map`, `typeclass_collections_demo`, `fizzbuzz`.

### W3 — F-RIGID + F-VALRESTR: signature rigidity and value restriction  [1-2S, CRITICAL, confirmed empirically]

- **F-RIGID evidence:** probe `sr_rigid` type-checks (env shows `main.g : Int` with
  `main.f : Int -> Int`) and at runtime prints a pointer+1 (`4331685068`).
- **Locations:** `check_fn_body` `infer.sprout:3491` instantiates declared prog-vars as
  FLEXIBLE fresh tyvars — the body may collapse them to concrete types with no error.
  `pre_scan_fn_decls` `infer.sprout:2839-2841` registers the declared polymorphic scheme
  for forward/mutual refs; `typecheck_decl` `infer.sprout:2940-2944` later overwrites it
  with the inferred type — but earlier decls already committed against the wrong scheme.
- **Fix sketch:** skolemize declared tyvars in `check_fn_body` (instantiate as rigid
  constants that unify only with themselves), producing a "signature is too general for
  body" error when the body constrains them. That single change makes the pre-scan
  scheme trustworthy (a checked signature is safe to commit against). Mind the
  `ProgVarName` vs `FreshTVarName` wrap seam when minting skolems.
- **F-VALRESTR evidence:** probe `sr_valrestr` — `let cell = mutvec_new(1, Nil)`
  generalizes to `forall a. MutVec (List a)`; Int written, String read; accepted.
- **Location:** `LetDecl` at `infer.sprout:2955-2967` — `unifier.generalize` on ANY
  expression; the initializer's effect is also discarded (`InferOk typed_expr s1 _ _`),
  silently violating the top-level-purity rule (full enforcement of that lands with W6).
- **Fix sketch:** syntactic value restriction — generalize only syntactic values
  (lambdas, literals, variables, constructors/tuples of values); monomorphic otherwise.
- **Blast-radius check:** stdlib/compiler may rely on generalizing non-value lets
  (e.g. `let x = dict_new()` idioms). Run the self-compile oracle early
  (`scripts/memwatch.sh 4096 1 -- <stage-1 self-compile>`); each rejection it surfaces
  is a genuine latent bug to fix at the use site, not a reason to weaken the rule.
- **Corollary (small, same session):** any typo'd lowercase type name in an annotation
  becomes an implicitly-bound tyvar (`is_lowercase_name`, `infer.sprout:191-198`), and
  FnDecl/ClassDecl signature names are unvalidated (admitted at `infer.sprout:2662`).
  With skolemization the typo at least fails inside the fn; extending §5.6 type-name
  validation to FnDecl/ClassDecl positions is the complete fix (BACKLOG already tracks it).
- **Regression tests:** `sr_rigid`, `sr_valrestr` must be REJECTED (negative tests);
  positive: polymorphic identity, mutual recursion with correct annotations still accepted.
- **Gates:** compiler change → full §4 battery.

### W4 — F-DISPATCH: class-method dict chosen by first concrete arg, not constraint position  [1S, CRITICAL, confirmed empirically]

- **Evidence:** probe `sr_dispatch` — `class Desc a { fn describe(tag: String, x: a) }`;
  `describe("tag", 42)` selects the **String** instance (IR stores the
  `..._Desc_String_describe` dict). With a body that uses `x` as String → heap UB.
- **Locations:** `check_instance_for_marker` `infer.sprout:761-777` input-position branch
  → `first_concrete_typed_arg_str` `infer.sprout:832-838` scans args left-to-right for
  any concrete head, never checking it sits at the class-var position; the post-pass
  (`maybe_rewrite_class_method_call` `infer.sprout:3288`) deliberately skips when
  `class_var_in_args`, so the wrong pick is final. `resolve.sprout` can't catch it (an
  instance for the wrong head EXISTS, so `check_constraint` passes).
- **Fix sketch:** the class decl gives the method's parameter types; find the parameter
  position(s) whose declared type mentions the class variable, and read the concrete head
  from the typed argument at THAT position (first such position with a concrete head).
- **Sibling (same session):** `scan_fwd_markers` `infer.sprout:1350-1365` returns the
  first `@fwd:*:Class` entry — with two same-class constraints (`Eq a, Eq b`) on a
  class-method call whose constraint var isn't at an arg head, the wrong forwarded dict
  can be picked. Key by prog var, mirroring the `resolve_obligation` fix that already
  landed for constrained functions.
- **Regression tests:** `sr_dispatch` must print `INT`; a two-constraint
  (`Eq a, Eq b`) method probe; existing typeclass suites must stay green.
- **Gates:** compiler change → full §4 battery (dict resolution is bootstrap-sensitive:
  self-compile oracle mandatory).

### W5 — F-EXH: exhaustiveness + unreachable-branch diagnostics  [1-2S, HIGH, confirmed empirically]

- **Evidence:** probes `sr_exh_nested` (`Just 1` + `Nothing` accepted; `Just 2` → runtime
  `abort_match`), `sr_exh_bool` (`true`-only match accepted), `sr_unreach` (branch after
  `_` accepted; spec §10.12 promises an error).
- **Locations:** `exhaustiveness_check` `infer.sprout:1915-1929` compares only top-level
  ctor-name sets (`collect_seen_ctors` :1891-1899); `extract_adt_name` :1871-1875 returns
  `Nothing` for `TTuple`/`TVar` scrutinees (silently skipped); Bool/Int/String literal
  matches: `dict_get("Bool", ctor_map)` misses → silently exhaustive; types absent from
  the bundled program's `decls` (:2633) also skipped. NO unreachability logic exists
  anywhere in `stdlib/compiler/`.
- **Fix sketch (minimum viable, staged):** (1) a branch with any literal subpattern does
  not "cover" its constructor — require a same-ctor catchall or full literal coverage
  (Bool: `true`+`false` is complete); (2) recurse into constructor/tuple subpatterns one
  level with the same rule applied recursively; (3) Bool scrutinee handled as a 2-ctor
  ADT; tuple scrutinees recurse per element; (4) implement the four spec-enumerated
  unreachability errors (after catchall / duplicate literal / duplicate ctor coverage /
  all ctors covered). A full Maranget usefulness matrix is the v1 end-state — record as
  follow-up if not reached, but stage (1)-(4) close every probe.
- **Regression tests:** the three probes rejected; spec §10.11/§10.12 examples as
  conformance fixtures; existing accepted matches stay accepted (watch prelude's big
  matches — run full suite early).
- **Gates:** compiler change → full §4 battery.

### W6 — F-EFF: effect-system enforcement campaign  [2-3S, CRITICAL, confirmed empirically; rollout per D2]

- **Evidence:** probe `sr_effect` (pure-declared fn calling `print`) → checker OK, env
  shows `main.sneaky : Int -> Int`. Probe `sr_mainpoly` (`fn main() -> Unit !{e}`) → OK
  despite spec.
- **Three independent holes, each must close:**
  1. `check_fn_body` `infer.sprout:3503` (and `LetDecl` :2960) discards the inferred body
     effect — never compared with `effect_from_maybe_labels(effects_maybe)`.
  2. `infer_call_var` `infer.sprout:581-598` instantiates only `scheme_type`;
     `scheme_effects` never consulted → calling `print` infers Pure.
  3. `unifier.sprout:185` unifies `TFunc` ignoring both effect fields; `unify_effects`
     :233-236 accepts `Pure ~ IO` both directions. Define the real v0 lattice: Pure and
     IO as closed rows; singleton effect var instantiable to either; mismatch = error.
- **Same campaign:** `merge_effects` `infer.sprout:340-346` drops one side for var/var
  and var/row merges (`| _ -> a`) — latent until enforcement, then live; top-level `let`
  initializer purity (spec §3) via the no-longer-discarded effect; `main` must reject
  effect polymorphism (spec §10.10).
- **Rollout (pending D2):** land all checks behind `SPROUT_EFFECTS_ENFORCE=1`; run suite
  + self-compile + examples under the flag; fix every flushed violation (each is a real
  mislabeled function — e.g. anything calling an `!{IO}` extern from a pure signature);
  flip default; delete flag. Keep per-step commits small (Collaboration §1).
- **Regression tests:** `sr_effect`, `sr_mainpoly` rejected; effect-polymorphic helper
  (`!{e}`) instantiated at both purity and IO accepted; pure-calls-pure accepted;
  typecheck-failure tests for each hole individually.
- **Gates:** compiler change → full §4 battery; expect multiple seed refreshes.

### W7 — F-DIV: division UB  [1S, CRITICAL, source-verified; blocked on D1]

- **Locations:** direct path `codegen.sprout:2056-2062` (bare `sdiv`); typed path
  `ast_to_ir.sprout:1861` (`IRIDiv`) → `ir_lowering.sprout:118` (bare `sdiv`);
  `sprout_ir.sprout:91-95` documents the UB pass-through as deliberate.
- **Facts for D1:** `x / 0` and `INT_MIN / -1` are LLVM UB (not even a reliable crash).
  `%` is dead code — the lexer/parser never produce it; `codegen.sprout:2063-2068`'s
  `srem` arm is unreachable (remove it or implement `%` end-to-end, not halfway).
- **Fix sketch (if D1 = panic):** emit a zero/overflow guard branching to a runtime
  panic-with-location helper in BOTH paths (or one shared runtime function
  `sprout_checked_div` — note that adding it touches APPROVED_BUILTINS rules; an
  IR-level guard avoids that). Update `sprout_ir.sprout` op documentation and spec §6.
- **Regression tests:** `1/0` panics with a message (run-level conformance test);
  `INT_MIN / -1` defined; constant `1/0` in a top-level let too (checks both const and
  runtime paths).
- **Gates:** compiler change → full §4 battery; spec update in same change (Docs&Spec §5).

### W8 — Prelude totality + complexity batch  [1S, mechanical; P1/P2 blocked on D5]

All `stdlib/prelude.sprout`. Every touched export gets/keeps a correct `# O(...)` comment
(project rule).

| ID | Item | Location | Fix |
|---|---|---|---|
| P1 | `split_ints` partial (junk→0 via bare `parse_int`) | :106 | per D5: `Result`/`Maybe` signature |
| P2 | `mutvec_get` no bounds check → C UB (`vector_get_direct`) | :1156 | per D5: `Maybe a !{IO}` via `vector_get`; audit `mutvec_set`/`vector_mutset` bounds too |
| P3 | `vec_sort_by` O-comment WRONG — `vec_append` rebuild is O(n²), dominates | :427-432 | rebuild via a linear-build primitive (investigate `vec_from_list`/MutVec freeze); fix comment |
| P4 | `ToString (List a)` O(n²) growing-`++` | :953-962 | accumulate parts, `string_concat_many` |
| P5 | `ToString (Vec a)` same | :971-983 | same |
| P6 | `ToString (Dict v)` same | :985-1001 | same |
| P7 | `mconcat` O(n²) for String | :561-562 | document O() honestly; consider `to_list` + `string_concat_many` fast path (design note — Monoid-generic, discuss if non-obvious) |
| P8 | `list_dedup` O(n²), undocumented | :362-370 | document; better algorithm needs Ord/Hash — note only |
| P9 | `Semigroup (Dict v)` append O(n²), undocumented | :862-876 | document; bulk-build if a primitive exists |
| P10 | `vec_map`/`vec_filter`/`vec_filter_map`/`vec_reverse`/`vec_slice` O(n²) via `vec_append`, no O() comments | :170-260 | linear-build primitive as P3; at minimum document |

- **Regression tests:** unit tests for P1/P2 behavior; for complexity items a comment fix
  needs no test, an algorithm fix needs an equivalence test (same output as old impl on
  sample data).
- **Gates:** stdlib change → fmt, full suite, compile-examples, seed refresh (stdlib is
  bundled into the compiler → seed gate applies).

### W9 — Lexer/parser diagnostics batch  [1S, mechanical]

| ID | Item | Location | Fix |
|---|---|---|---|
| X1 | Template `\r` silently becomes letter `r` (string literals decode CR) | `lexer.sprout:77-85` vs `:137-143` | unify escape tables |
| X2 | Template `\0` passes a NUL through — violates the no-NUL String invariant (string literals reject it) | `lexer.sprout:137-143` | reject like `decode_escape` |
| X3 | Raw newline accepted inside string literal → distant confusing error on unterminated strings | `lexer.sprout:118-132` | error "unterminated string literal at line N" on `\n` |
| X4 | Integer literal overflow silently wraps (`parse_int` C-level) | `parser.sprout:705` | detect overflow (length pre-check or checked parse) → compile error |
| X5 | `wrap_where_binding` emits `SourcePos(0,0,0)` for non-variable where-patterns | `parser.sprout:1136` | thread the pattern's own span |
| X6 | `tok_at` OOB fallback token at `SourcePos(0,0,0)` → "line 0:0" diagnostics | `parser.sprout:20-22` | fall back to last real token's position |

- **Regression tests:** parser/lexer tests per item (X1-X4 behavioral, X5/X6 assert the
  span in the error output).
- **Gates:** compiler change → full §4 battery.

### W10 — Direct-codegen-path batch  [1S; blocked on D3]

All static findings (agent lost run permissions mid-review) — each needs an empirical
repro under `--use-direct-codegen` as its DoR step.

- **C2 — width-3 sret ABI mismatch:** `emit_match_unboxed_call` `codegen.sprout:2401-2426`
  calls `{i64,i64,i64}`-returning externs DIRECT while `emit_extern_decls` :3955-3956
  declares them sret → misread return registers. Worker-mode caller was fixed
  (`emit_worker_cpr_do_sret` :3652-3657); this site was missed. Blast radius today: only
  `native_set_to_list` (sole width-3 allowlisted extern). Fix: route width==3 through the
  same sret helper. Cross-check with `scripts/cpr_differential_check.sh`.
- **C3 — TCO slot for tuple-typed param never rooted:** `allocate_tco_slots_acc`
  :3247-3261 roots i64 and ptr slots but not struct slots → tuples built inside a TCO
  loop are unrooted across iterations ≥2. Fix: push scan roots for struct fields like
  `push_temp_root` :879-888 does.
- **C4 — pending-TCO escape:** (a) `emit_fn_tco` :3214-3221 pops entry roots INSIDE the
  loop each iteration (root-stack underflow into enclosing frames) for bodies whose value
  reaches top level still carrying `tco_args` (do-chain ending in unconditional
  self-call); (b) `emit_pending_tco` :1733-1742 neither clears `tco_args` nor checks
  `is_terminated` → double back-edge emission after terminator (verifier failure) with
  ≥2 nested let-steps ending in a bare self-call.
- **C5 — `append` silent fallbacks (BOTH paths, do even if D3=retire):** direct path
  keys on leaf name before `fn_sigs` (`codegen.sprout:2530`) — hijacks any user fn named
  `append`, and non-String/List semigroup operands silently become `0` (:2609/:2626);
  typed path has the acknowledged IRConst-0 placeholder (`ast_to_ir.sprout:3634-3640`).
  Fix: consult `fn_sigs` first; loud panic for unhandled semigroup shapes (same policy
  as the `unresolved call` panic at :2601).
- **C6 — `emit_var` silent fallbacks:** :1854 null-closure when fn missing from
  `fn_wrap` (reachable for `main`-as-value; this is literally the `str_concat(ptr null,…)`
  smoke-gate shape); :1900 unknown var → `zero_val`. Both → loud panic.
- **C8 — `@str_compare` declared `(ptr,ptr)` vs C `(long long, long long)`:**
  `codegen.sprout:3365` — known/BACKLOG'd; fix while here (typed path already correct).
- **C9 — `ir_rooting.rewrite_ops` silently truncates a block if `live_afters` runs
  short:** `ir_rooting.sprout:757-761` — unreachable today, but silent-wrong-code shape;
  make it panic.

### W11 — Type-system hardening (mediums)  [batch opportunistically]

- **T7 — `unify_applied` compares TConst names modulo module prefix:**
  `unifier.sprout:182-183` (`string.after_last_dot(a) == string.after_last_dot(b)`) —
  two same-named types (incl. `wrap`s) in different modules unify silently. The one hole
  in wrap opacity (same-module wraps are correctly opaque). CARE: find out why prefix
  stripping was needed (bundler dot-prefix legacy?) before changing; self-compile oracle
  mandatory.
- **T8 — pattern variables share the substitution namespace with fresh tyvars:**
  `infer.sprout:2050` keys the subst by source identifier; fresh vars are `"t"++N`
  (`unifier.sprout:29`) — a user binding `t0`/`t1` can clobber a live fresh binding
  (realistic in importless files where the counter starts low). Fix: distinct namespace
  for fresh vars (illegal-in-surface prefix) or apply the existing
  `FreshTVarName` wrap discipline at this boundary.
- **T10 — `find_inst_in_return_type_children` recurses only into the TApp argument,
  never the base:** `infer.sprout:3218-3223` — multi-param TApp chains (`Result e a`
  needing an instance on `e`) miss the concrete dict and fall back to @fwd; worst case a
  late missing-dict failure (not wrong-dict). Fix: recurse both sides.
- **T11 — `check_overlapping_instances` scans only the current program's decls**
  (`infer.sprout:2802`) — sound under today's whole-program bundling; breaks silently if
  a non-bundled path ever lands (`register_instance_marker` is a plain `dict_set`
  overwrite). Note for the iface arc; no action now.

---

## 4. Process gates for every session (non-negotiable)

1. **DoR:** regression test written and confirmed failing for the right reason BEFORE the fix.
2. **Compiler-source changes** (`stdlib/compiler/` or bundled `stdlib/*.sprout`):
   run `just refresh-seed` BEFORE `mise exec -- just test` (stage-1 uses the committed
   seed; new behavior is invisible until refresh). Delete `build/compile_driver_bin_stage1`
   first — the mtime guard silently reuses stale binaries. If the committed seed predates
   a parser change, use the 2-step bootstrap protocol (docs/debugging.md).
3. `mise exec -- just fmt` before test; `mise exec -- just test` EXACTLY ONCE (output
   lands in `/tmp/sprout_test_<session_id>.txt` — read that, don't re-run); no concurrent
   test runs.
4. Compiler changes: smoke shapes + bundle smoke (AGENTS.md DoD 7-8); rooting-adjacent
   changes (W1, C3, C4, C9): `just test-stress` — default-GC greens are false negatives
   for rooting bugs; `SPROUT_GC_STRESS=1` is the oracle.
5. Runtime changes: example canary (compile AND run): tuples, factorial, maybe_map,
   typeclass_collections_demo, fizzbuzz.
6. Memory-risky commands (self-compile, bootstrap) through
   `scripts/memwatch.sh 4096 1 -- <cmd>`.
7. Spec/docs updated in the same change when behavior changes (W5, W6, W7 all change
   diagnostics/semantics → spec §5.5/§6/§7 updates required). Update BACKLOG.md in the
   same commit as each landed workstream.
8. Commits per AGENTS.md style; PRs via `tea` (Codeberg, not GitHub); seed gate hook
   will block stale-seed commits.

## 5. Suggested session schedule

| Session | Workstream | Blocked on |
|---|---|---|
| 1 | W1 global GC roots | — |
| 2 | W2 runtime UTF-8 (R1+R3+R4 now; R2 when D4 lands) | D4 partial |
| 3 | W4 dispatch-by-constraint-position | — |
| 4-5 | W3 rigidity + value restriction | — |
| 6 | W5 exhaustiveness + unreachability | — |
| 7 | W8 prelude batch | D5 for P1/P2 |
| 8 | W9 lexer/parser batch | — |
| 9-11 | W6 effects campaign | D2 |
| 12 | W7 division | D1 |
| 13 | W10 direct-path batch | D3 |
| — | W11 mediums | fold into adjacent sessions |

Rationale for the order: W1 is the worst confirmed corruption with the smallest fix; W4
before W3 because it's one localized wrong-selection bug with a crisp test; W3 before W6
because skolemization changes what W6's enforcement sees; W6 late because it's the
largest blast radius and D2 shapes it.

## Appendix A — probe programs (canonical copies)

`sr_effect.sprout` — must be REJECTED after W6:
```sprout
module main

# A pure-declared function (no !{IO}) that calls print — should be rejected.
fn sneaky(x: Int) -> Int =
  do
    print("side effect from pure fn")
    x + 1

fn main() -> Unit !{IO} =
  do
    print(int_to_string(sneaky(1)))
```

`sr_rigid.sprout` — must be REJECTED after W3 (today: prints pointer+1 garbage):
```sprout
module main

# g calls f via the forward-reference (pre-scan) scheme: forall a. a -> Int.
# f's body forces x to Int, but g already committed to f(String).
fn g() -> Int = f("hello")

fn f(x: a) -> Int = x + 1

fn main() -> Unit !{IO} =
  print(int_to_string(g()))
```

`sr_valrestr.sprout` — must be REJECTED after W3:
```sprout
module main

# Top-level let of a mutable, IO-effectful expression. If generalized
# (no value restriction), cell : forall a. MutVec (List a) and can be
# used at both List Int and List String.
let cell = mutvec_new(1, Nil)

fn stash() -> Unit !{IO} =
  mutvec_set(cell, 0, Cons(42, Nil))

fn read_back() -> String !{IO} =
  do
    xs <- mutvec_get(cell, 0)
    match xs with
    | Cons s _ -> s
    | Nil -> "empty"

fn main() -> Unit !{IO} =
  do
    stash()
    s <- read_back()
    print(s)
```

`sr_dispatch.sprout` — must print `INT` after W4 (today: prints `STRING`):
```sprout
module main

class Desc a {
  fn describe(tag: String, x: a) -> String
}

instance Desc String {
  fn describe(tag: String, x: String) -> String = "STRING"
}

instance Desc Int {
  fn describe(tag: String, x: Int) -> String = "INT"
}

# Class var `a` is the SECOND parameter; the first concrete arg is the
# String tag. If dispatch picks the first concrete arg's head, this call
# selects the Desc String dict instead of Desc Int.
fn main() -> Unit !{IO} =
  print(describe("tag", 42))
```

`sr_exh_nested.sprout` — must be REJECTED after W5:
```sprout
module main

# Non-exhaustive: Just(2) is not covered, but top-level ctor names {Just, Nothing} are all "seen".
fn h(m: Maybe Int) -> Int =
  match m with
  | Just 1 -> 1
  | Nothing -> 0

fn main() -> Unit !{IO} =
  print(int_to_string(h(Just(2))))
```

`sr_exh_bool.sprout` — must be REJECTED after W5:
```sprout
module main

# Non-exhaustive match on Bool literal: false branch missing, no catchall.
fn h(b: Bool) -> Int =
  match b with
  | true -> 1

fn main() -> Unit !{IO} =
  print(int_to_string(h(false)))
```

`sr_unreach.sprout` — must be REJECTED after W5:
```sprout
module main

# Branch after catchall is unreachable; spec enumerates unreachable-branch errors.
fn h(b: Bool) -> Int =
  match b with
  | _ -> 1
  | true -> 2

fn main() -> Unit !{IO} =
  print(int_to_string(h(true)))
```

`sr_mainpoly.sprout` — must be REJECTED after W6:
```sprout
module main

# Spec: effect-polymorphic main must be rejected.
fn main() -> Unit !{e} =
  ()
```

`sr_globalroot.sprout` — must print `x` then `hello, world` under `SPROUT_GC_STRESS=1`
after W1 (today: prints `x` twice — the global is swept and its memory reused):
```sprout
module main

let greeting = "hello, " ++ "world"

fn churn(n: Int, acc: String) -> String =
  if n == 0 then acc else churn(n - 1, acc ++ "x")

fn main() -> Unit !{IO} =
  do
    let junk = churn(500, "")
    print(str_slice(junk, 0, 1))
    print(greeting)
```

## Appendix B — refuted candidates (verified clean; do NOT re-investigate)

Runtime: `read_file` error paths & validation; GC rooting in `bst_*`/`vector_*`/
`crypto_*`/analysis helpers; `proc_run_vec` rooting; crash-handler async-signal-safety;
JSON surrogate parsing; base64 bounds; `utf8_validate` itself; `tcp_read` buffer bounds;
partial-write loops; `str_slice_bytes` boundary checks; growbuf overflow guards.

Type system: wrap opacity within a module (direct `Money ~ Int` correctly rejected);
occurs check (present, correct, subst-aware); `--emit-ir` does not bypass the checker;
@fwd:/@eta_fwd: namespace discipline intact; `resolve_obligation` recursive-constrained
null-dict fix has no bare-fallthrough sibling for constrained FUNCTIONS (class METHODS
are W4); resolve.sprout nested-constraint discharge correct for concrete heads;
overlapping instances within one bundled program rejected; scheme instantiation
capture-free; tuple exact-arity enforced.

Codegen: typed-path Char comparisons; duplicate `entry:` for trivial tuple accessors
(empirically clean now — the old memory about it is stale); direct-path IRLoadGlobal
classification; phi predecessor bookkeeping (ctor tests, short-circuit, if, do-bind);
scrutinee liveness across match arms; single-pass liveness vs TCO back-edge;
`op_exposes_operands(IRApplyClosure)=false`; root-once head-invariant;
`print(tuple)`; `eprint` coercion; `str_concat`/`str_eq` operand rooting;
do-bind phi types; typed-path TCO detection (tokenize_from-class shapes handled).

Parser/lexer/prelude: `Ord` instances for Maybe/Result/Bool/Vec consistent with Eq;
`tokenize_from` always advances; `|>` left-assoc and `>>`/`<<` right-assoc correct;
`--x` no loop; template `${` scanning; `dict_set` arg order.
