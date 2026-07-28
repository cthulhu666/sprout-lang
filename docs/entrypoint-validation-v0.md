# Executable entrypoint validation (v0)

Status: **normative for a defined `main`** (implemented). Missing-main enforcement: deferred (see §4).

## 1. Problem

The self-hosted compiler performed no validation of `main`. Codegen dispatched
on the *declared* return-type name only (`ast_to_ir.user_main_fn_name`), so a
malformed `main` was silently miscompiled:

- `fn main(x: Int) -> Unit !{IO}` — extra args ignored, `x` bound to `0`.
- `fn main() -> String !{IO}` / `-> Maybe String !{IO}` — non-`Unit`/`Int` body forced through the `Unit` wrapper.
- `fn main() -> Unit` — pure `main` accepted (declares no effect).
- `fn main() -> Unit !{e}` — effect-polymorphic `main` accepted.

`spec-v0.md` §10.10 already *specified* the correct rules; they were simply
unenforced. The now-obsolete `conformance/executable_error` corpus was written
for exactly these cases (its diagnostics vanished with the retired reference
compiler).

## 2. Rules (on a DEFINED `main`)

A function named `main` (bare, or the entry module's qualified `<mod>.main`) is
only ever an intended process entrypoint, so if defined it must satisfy:

| Rule | Diagnostic |
|---|---|
| zero parameters | ``Executable entrypoint `main` must take zero arguments`` |
| return type ∈ {`Unit`, `Int`} | ``… must return Unit or Int`` |
| effect row contains `IO` (not pure) | ``… must declare the {IO} effect`` |
| effect row not a bare variable | ``… must not be effect-polymorphic`` |

An `Int` return is the process exit code; any other type would miscompile through
the `Unit` wrapper. Prior art: Haskell (`main :: IO ()`), Rust (`fn main()` → `()`
or `Termination`, no args), Go (`func main()`, no args/return) — all require zero
value-args and a unit/exit-code return in a designated entry module.

## 3. Placement — a final gate on an otherwise-valid program

`validate_entrypoint` (in `infer.sprout`) runs inside `typecheck_decls`, but
**only on `TypedOk`** — i.e. after the whole program, including `main`'s body,
has typechecked. Rationale:

- A broken `main` body (e.g. an under-application error) reports *its own* error
  first; the entrypoint gate never masks it. This also avoids breaking existing
  `type_error` fixtures that use a pure `fn main() -> Int = …` as their vehicle —
  they fail the body typecheck and never reach the gate.
- It reads the *declared* signature (params / `return_type` / `effects` fields of
  `ast.FnDecl`), so no inference of the effect row is needed; the parser already
  splits `-> Unit !{IO}` into `return_type = Unit`, `effects = ["IO"]`.

## 4. Why "missing main" is deferred

Requiring a `main` **cannot** be a type-check error. `validate_entrypoint` runs
in `typecheck_decls`, which serves both `--phase check` (type-check a *library* —
no `main` expected) and `--emit-ir` (build an *executable* — `main` expected)
identically; at that point the checker cannot tell which intent it serves.
Main-less files are legitimate — every library module, and two shipped examples
(`examples/sentry_api.sprout`, `examples/sentry_issue_browser_tui.sprout`).

"Missing main" is therefore a property of *"am I building an executable?"*, an
`--emit-ir`/codegen concern (`main_shim` already computes `has_user_main`), not a
type-check concern. Enforcing it needs an explicit executable-vs-library compile
mode threaded from the driver. Until then, `executable_error/missing_main` is
xfailed in the `test-executable-errors` gate.

## 5. Tests

- Negative: `conformance/executable_error/{main_arity_mismatch, main_int_entrypoint,
  main_pure_entrypoint, main_effect_polymorphic_entrypoint, stdlib_main_maybe_entrypoint}`
  gated by `test-executable-errors` (`--phase check`, substring-match on `.err`);
  `missing_main` xfailed.
- Positive: `conformance/run/main_int_exit_ok.spr` (an `Int !{IO}` main runs to
  completion), plus every existing `Unit !{IO}` test main in the suite.
