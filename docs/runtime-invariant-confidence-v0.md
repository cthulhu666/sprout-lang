# Confidence for bootstrap-critical runtime/codegen changes

Status: **supporting design doc** — non-normative. Records what tooling gives (and would
further give) confidence to make invariant-changing runtime + codegen changes, using the
string-header migration (`BACKLOG.md`: "every Sprout String is now headered") as the case study.

## Why this class of change is scary

The string-header change had every property that makes a change high-stakes:

1. **Bootstrap-critical.** The compiler compiles itself; a miscompile corrupts the tool that
   builds the tool. `refresh-seed` rebuilds stage-1 from the *committed* seed, so a runtime that
   trusts a not-yet-established invariant miscompiles the transitional binary's own data.
2. **Silent failure mode.** Reading a length from `payload-8` on a *bare* (headerless) string
   returns a garbage number, not a crash — a wrong-answer bug, the hardest kind to notice.
3. **Whole-program invariant.** "Every String is headered" can be violated by any one of dozens
   of string producers; correctness is non-local.

The change was safe to make not because it was small (it was not) but because the failure mode
could be made **loud and immediate**. That is the whole game: *convert silent, non-local
corruption into a loud, local signal.*

## What made THIS change safe (available today)

- **`SPROUT_GC_HDRCHECK=1` — the invariant enforcer.** `str_byte_len` (and the GC sweep) already
  strlen-check the header `aux` against the actual bytes and `abort()` on mismatch. Running the
  full suite + example canary under this env var turns "a bare producer slipped through" from a
  1-in-256 silent wrong length into a guaranteed abort at the first offending call. This is what
  let us *drop the arena-membership check* with confidence: the audit found the producers, and
  HDRCHECK proved the audit complete across every exercised path (zero aborts).
- **The self-hosting fixed point.** `refresh-seed` iterating to a fixed point is itself a
  whole-compiler correctness test: the compiler recompiled *itself* with the new representation
  and converged, exercising essentially all string handling.
- **Two-phase sequencing forced by the bootstrap.** Codegen (headered literals) had to land and be
  baked into the seed *before* the runtime could trust `payload-8` — otherwise the transitional
  binary's own bare literals miscompile. Phase A (behavior-preserving, runtime unchanged) → seed
  refresh → Phase B (runtime trust-flip). The sequencing is not optional; it is dictated by
  `refresh-seed` rebuilding from the old seed.
- **A behavior-pinning test written first (TDD guard).** `tests/stdlib/test_byte_length.spr`
  covers literals (incl. multi-byte café/emoji, which corrupt if the payload GEP is mis-offset),
  concat, slice, and interned map keys — passing on the baseline and after each phase.

## What would raise confidence further (ranked by leverage)

1. **Turn on HDRCHECK (and equivalents) in CI.** The single highest-leverage lever: run the full
   suite + example canary under `SPROUT_GC_HDRCHECK=1` on every PR. The enforcer already exists;
   it is just off by default. Generalize to a "debug-assertions" runtime build that checks every
   invariant the runtime relies on (header kinds, arities, tags) and is exercised in CI.
2. **A *run* tier for the example canary.** `compile-examples` only compiles; DoD #11 (compile
   *and run* the canary set) is a manual gate. Automating it — run representative programs with
   input fixtures — closes the exact blind spot that historically hid runtime-only bugs
   (cf. `docs/retro-dict-dispatch-soundness-2026-07-13.md`: a wrong dictionary was a runtime
   SIGSEGV invisible to compile-only gates).
3. **ASan/UBSan build of the C runtime in CI.** A `payload-8` read or any pointer error would be
   caught immediately. A safety-first language's runtime should run its tests under sanitizers.
4. **A typed-IR / Core-lint verifier (the deep fix).** Elaborate the IR and typecheck it so a
   property like "every value flowing to `str_byte_len` is provably a CSTR" is a *compile* error,
   not a runtime coin-flip. This is the dispatch-soundness retro's top recommendation and would
   have flagged all bare producers statically instead of by audit-plus-HDRCHECK.
5. **Golden-IR diff for codegen changes.** Emit IR for a fixed corpus and diff against a committed
   golden, so a representation change's blast radius is *visible and reviewable* rather than
   trusted. `test_ir_codegen_strings.spr` is a hand-rolled instance of this; a general corpus diff
   would generalize it.
6. **Transactional bootstrap.** A failed bootstrap can delete the only working stage-1 binary
   (see the builtin-removal bridge in `docs/debugging.md`). Bootstrap should never destroy the
   last-good binary and should auto-verify the fixed point with easy rollback.

## The general principle

For any invariant-changing runtime/codegen work, before writing the change ask: **what makes a
violation loud, and where does it fire?** If the answer is "nothing — it silently returns a wrong
value," build the enforcer first (an assertion mode, a verifier, a differential test) and gate the
change behind it. The string-header change was tractable precisely because HDRCHECK already
answered that question; the levers above are about making that answer exist by default, for every
change, rather than being rediscovered per change.
