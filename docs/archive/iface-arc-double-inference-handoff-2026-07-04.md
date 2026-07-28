# Handoff — eliminate double HM inference via the iface arc (2026-07-04)

**One-line problem:** every full compile HM-infers the prelude and all imported
modules **twice** — once in the module loader (to extract exported schemes) and
again in the whole-program typecheck (because the bundle carries their decls).
The correct long-term fix is the **typed-interface (iface) arc**: persist a
module's typed AST / interface and *load* it instead of re-inferring.

This doc is for a follow-up session. Facts below were traced at the file:line
given; verify they still hold before building on them.

## What the pipeline does today (verified 2026-07-04)

1. **Per-module inference (for schemes).** `module_loader.load_module`
   (`stdlib/compiler/module_loader.sprout:313`) runs full HM inference on every
   imported module via `checker.check_program_with_env` (`:338`) to produce that
   module's exported `(name, Scheme)` pairs, cached in a `ModuleCache`.
   `load_prelude_pairs` triggers this for `stdlib.prelude` (+ its imports).
2. **Bundling includes everything.** `bundler.collect_modules`
   (`stdlib/compiler/bundler.sprout:489`) collects the entry + all transitive
   imports' decls, and (when any module has a module name) reads/parses the
   **prelude** and prepends it as a `ParsedModule` with full decls (`:507-525`).
   So the bundle = prelude decls + all imports' decls + entry decls.
3. **Whole-program inference.** `compiler.compile_phase_check`
   (`stdlib/compiler/compiler.sprout:218`) calls
   `checker.typecheck_typed(bundle, prelude_pairs)` (`:227`) →
   `infer.typecheck_decls` over the *entire* bundle — re-inferring the same
   prelude/import decls `load_module` already inferred in step 1.

**Net:** prelude + imports inferred twice per compile. The `ModuleCache` is
per-compile (`new_cache()`, `module_loader.sprout:299`), so there is no
cross-compile reuse either — the immutable prelude is re-inferred from scratch,
twice, on every run.

## Two things that are NOT the problem (don't chase them)

- **No third pass.** The old post-lowering recheck is gone: `compile_phase_recheck`
  is a plain alias for `compile_phase_lower` (`compiler.sprout:245-246`) because
  lowering preserves types. The `codegen.sprout:18` comment showing
  `→ typecheck_typed(lowered) →` is **stale** — fix or delete it while here.
- **The bundler does not typecheck.** The old "inference in the bundler" framing
  is wrong; the per-module inference lives in `module_loader`.

## Why the iface arc is the right fix (not a hand-optimization)

- The two passes produce *different outputs* (interface schemes vs. typed AST for
  codegen) from the *same computation*. The iface arc computes the typed AST
  once, persists it, and derives the interface from it — removing the duplicate
  inference at the root.
- Do **not** try to skip the bundled prelude/import decls inside `typecheck_typed`
  to dedupe. Whole-program bundling is what currently guarantees cross-module
  typeclass coherence / polymorphic-recursion soundness "for free"; peeling
  modules out of it re-opens separate-compilation correctness that the iface
  format must handle deliberately. See [[project_iface_arc_strategic_direction]]
  and [[project_iface_format_design_rules]] (version int + loud failure, content
  hashes not mtime, forward-compat CI from day one).

## Suggested first increment (smallest real win)

Persist the **prelude/stdlib typed interface across compiles** and have
`load_module` load it instead of calling `check_program_with_env`, keyed by a
content hash (not mtime). The prelude is immutable within a run and rarely
changes across runs, so this removes the largest, most-repeated slice of the
duplication with the least correctness surface. There is already an
`--emit-iface` / `--check-iface` CLI surface in `compile_driver.sprout` to build
on. Measure before/after with the GC/inference profiling from
[[project_gc_profile_findings_2026_07]] (self-compile is inference/GC-heavy).

## Open question to nail down first

I traced the *inputs* (bundle contents + both inference call sites) but did not
instrument to confirm the whole-program pass isn't partially short-circuited for
names already present via `prelude_pairs` (source decls take precedence —
`checker.sprout:670`). Before sizing the win, add a counter to
`infer.typecheck_decls` (decls inferred per compile) and confirm the prelude
decls are actually re-inferred, and by how much. That number is the ROI estimate.

Design references: `docs/spec-v0.md`, plus the iface memory notes above.
