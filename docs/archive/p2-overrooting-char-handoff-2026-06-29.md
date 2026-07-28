# Handoff: typed-codegen over-rooting (P2) + `Char`-as-scalar — next session

Entry point for the next typed-codegen rooting work. The detailed designs live in
two companion docs; this doc is the **plan, sequencing, state, and guardrails**.

- Over-rooting precision design → `docs/archive/p11-over-rooting-handoff-2026-06-28.md`
- `Char`-as-immediate-codepoint design → `docs/archive/char-scalar-representation-followup.md`

## Why these are ONE piece of work

The typed path over-roots ~2.76× vs direct (correctness-safe over-retention, not
UAF). `Char` is the single largest avoidable contributor: it is currently a heap
value (rooted everywhere, even immortal ASCII chars — the lexer touches every
source char). Making `Char` an immediate `i64` codepoint **removes that entire
root class** instead of making it more precise — so it belongs in the same effort
as the over-rooting precision pass, not as a separate optimization.

## State at handoff (2026-06-29)

- **PR #101** (`fix/typed-codegen-tco`): the flip + TCO + memory fix + two
  GC-stress UAF fixes (Char-heap classification, phi-operand liveness). Committed
  `459f9a6`, force-pushed. **This work depends on #101 landing first** — branch off
  master once it merges (do NOT stack on the unmerged branch).
- Current measured state: whole-compiler peak RSS ~1.35 GB (direct ~305 MB);
  `ir_runtime_parity` 110/110, 0 TYPED-*; `just test-stress` 5/5.
- `Char` is heap and correctly rooted *today*. Phi-operand propagation is now
  unconditional (added a little over-rooting — see guardrails).

## MANDATORY invariants — do not break

1. **`Char` stays heap-classified until the representation change actually lands.**
   The over-rooting precision pass must **NOT** re-add `Char` to the non-heap-scalar
   whitelist as a rooting shortcut — that reintroduces the swept-multi-byte-char
   UAF. The only correct way to make `Char` scalar is the full representation change
   (runtime + literal lowering + equality coercion), done atomically.
2. **Phi operands must stay live-out of the predecessor they are chosen on.**
   `rewrite_phi_in_set` is now unconditional. Tightening it to "only when the phi
   result is live" is legitimate precision work, but "live" must mean *live-out OR
   locally-used-after-the-phi* — do NOT revert to the old `live_in`-only guard
   (that dropped locally-consumed phi operands → the `take_while` SourceCursor UAF).
3. **Oracle = `just test-stress` (SPROUT_GC_STRESS=1).** A passing default `just
   test` is timing luck and hides UAFs. Run stress after every iteration. See
   `project_gc_stress_oracle` and the GC free-tracer (`just gc-trace <file> <fn>`).
4. **Keep `ir_runtime_parity` 110/110, 0 TYPED-*.** Any drop is a real miscompile.

## Recommended order

Land #101 → branch off master → then:

1. **`Char` → immediate codepoint FIRST.** Removes a whole root + allocation class,
   so the subsequent precision audit operates on a smaller, cleaner set (you
   measure once on the reduced baseline instead of twice). Steps are in the
   companion doc; in brief: runtime `char_from_codepoint`=identity + `char_to_str`/
   `char_to_string` as the only alloc points; `'a'`→`i64` const in both backends;
   drop `is_char_type` from the string-coercion paths for `==`/printing; restore
   `Char` to the non-heap-scalar predicates (both `type_kind` AND the
   `codegen.sprout:846` duplicate — see latent issue #2). Full GC-stress + parity.
2. **Re-measure** root count on `codegen.sprout` + whole-compiler RSS.
3. **Over-rooting precision** on what remains — the companion P2 doc's options A
   (persistent root frame across consecutive triggers — the big payoff), B (tighten
   `op_exposes_operands`), C (liveness precision audit, now including phi-operand
   precision per invariant #2). Target: well under 1 GB.

## Latent issues found this session — fold into the work

1. **Single-pass liveness over TCO back-edges (correctness probe).**
   `ir_rooting.compute_liveness` is a single reverse-topo pass that assumes a DAG,
   but TCO introduces a back-edge (`IRTcoBack → tco_loop`). It did NOT bite the
   `take_while` bug (cursor flowed via a forward phi), but a heap value live *across*
   a back-edge could be under-rooted with no fixpoint iteration. **Before trusting
   TCO'd loops with loop-carried heap state, write a targeted GC-stress probe**
   (a TCO'd loop that carries a heap accumulator used only after the back-edge).
   If it reproduces, the fix is fixpoint iteration (or a back-edge-aware pass).
2. **Duplicate `type_is_non_heap_scalar`** in `type_kind.sprout:48` and
   `codegen.sprout:846` (latter still live, used at `codegen:896,1228`). The
   `Char`-scalar change touches both anyway — de-duplicate then (make `codegen`
   call `type_kind`).

## Method / DoD

Follow the companion P2 doc's DoD: refresh-seed (delete
`build/compile_driver_bin_stage1` first) → `just test`, `just test-stress`,
`scripts/ir_runtime_parity.sh` (110/110, 0 TYPED-*), `just flip-readiness`,
smoke-shapes, bundle-smoke, compile-examples-stage1. TDD-guard requires touching a
`tests/` file before editing `stdlib/compiler/`. Each fix its own commit.

## Key code map

- `stdlib/compiler/ir_rooting.sprout` — the rooting pass. `op_triggers_gc` :30,
  `op_produces_simple_heap` :96, `op_exposes_operands` :~545, `rewrite_phi_in_set`
  (now unconditional), `compute_liveness` :~434 (single-pass — see latent #1),
  `block_live_afters` :~464.
- `stdlib/compiler/type_kind.sprout` — the non-heap-scalar predicates (Char now
  excluded = heap).
- `stdlib/compiler/codegen.sprout:846` — direct-backend duplicate predicate.
- `tests/stdlib/test_ir_codegen_char_rooting.spr` — Char/lexer GC-stress
  regression (gated in `just test-stress`).
- Runtime `char_from_codepoint`/`char_to_str`/`g_ascii_char_strs` — the Char
  representation to change.
