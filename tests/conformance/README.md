# Conformance corpora

Executable language-behavior fixtures. Each subdirectory is a distinct *kind* of
check with its own harness. **Every corpus below must be wired into a gate** —
an unrun corpus silently rots as the language moves (fixtures drift onto removed
syntax, goldens go stale) and nobody sees red. If you add a subdirectory, wire it
into `justfile` and `ci-fast-gates` in the same change.

| Directory | Checks | Harness (justfile) | In CI |
|---|---|---|---|
| `run/` | golden stdout: compile → link → run, stdout must equal `<name>.out` byte-for-byte | `test-conformance-run` | ✅ `ci-fast-gates` |
| `type_error/` | negative: `--phase check` output contains `<name>.err` substring | `test-type-errors` → `_test-reject` | ✅ `ci-fast-gates` |
| `parse_error/` | negative: parse-phase rejection, output contains `<name>.err` | `test-parse-errors` → `_test-reject` | ✅ `ci-fast-gates` |
| `package_resolution/` | second-root module resolution | `test-package-resolution` | ✅ (`test`) |
| `executable_error/` | negative: a **defined** `main` with a malformed signature (nonzero args, non-`Unit`/`Int` return, pure, effect-polymorphic) is rejected by `validate_entrypoint`; output contains `<name>.err` | `test-executable-errors` → `_test-reject` | ✅ `ci-fast-gates` |
| `runtime_error/` | ⚠️ **obsolete** — predates the self-hosted compiler; nothing runs it | none | ❌ |
| `parity_*/` | ⚠️ **obsolete** — byte-parity vs the retired reference compiler; no golden | none | ❌ |

## Adding a fixture

- **`run/`** — write `<name>.spr` and its expected stdout `<name>.out`. Every
  fixture gets the **prelude**, headerless or not
  ([prelude-scope-v0.md](../../docs/prelude-scope-v0.md)), so prelude names and
  constructors are simply available. Redefining one shadows it, and the fixture's
  own declarations are qualified under a synthetic module name that is stripped
  from all output — so `print(Just(3))` prints `Just(3)`.

  Add `no_prelude` as the first line if the fixture needs to be preludeless. Two
  cases genuinely do, both because the construct resolves by *unqualified* name:
  a fixture that redefines a type and binds it with `<-`, and one that declares
  its own `class`. `codegen_do_bind.spr`, `instance_check.spr` and
  `type_classes.spr` are the in-tree examples.

  > Until 2026-08-20 this said bare `.spr` files were self-contained and got no
  > prelude, so a fixture had to define everything it used. That is what put all
  > 8 entries in `run/XFAIL`; the manifest is now empty.
- **`type_error/` · `parse_error/` · `executable_error/`** — write `<name>.spr` and
  `<name>.err` holding a stable substring of the expected diagnostic (matched with
  `grep -F`, so pick a message fragment, not the line/column suffix).

## `run/XFAIL` — visible quarantine

`test-conformance-run` reads `run/XFAIL` (one basename per line, `#` comments
allowed). A listed fixture is expected to fail; if it starts **passing** again the
gate goes RED (`UNEXPECTED PASS — remove from XFAIL`), so quarantine self-heals.
An orphan `.out` with no sibling `.spr` also fails the gate. Quarantine is for
tracked, documented rot — not a place to hide new failures.

## `_test-reject` xfail — the negative corpora's quarantine

`_test-reject` takes an xfail list of basenames (a fixture whose expected diagnostic
the compiler does not yet produce). Like `run/XFAIL` it self-heals: a listed fixture
that starts matching reports `UNEXPECTED MATCH (remove from xfail)` and the gate goes
RED. Unlike `run/XFAIL` the list lives in the `justfile` recipe, not in a file here.
`type_error/` and `parse_error/` pass an empty list; `executable_error/` xfails
`missing_main` only, because rejecting a *missing* `main` needs an
executable-vs-library compile mode that does not exist yet — a library module
legitimately has none (e.g. `examples/sentry_api.sprout`). Tracked in `BACKLOG.md` §7.3.

## Obsolete corpora (⚠️)

`runtime_error/` and `parity_*/` predate the self-hosted compiler and are **not
gated**. Their disposition (delete, or regenerate goldens) is tracked in
`BACKLOG.md` §7.3. Do not treat their presence as coverage.

`executable_error/` was listed here until 2026-08-16 and should not have been:
entrypoint-signature validation was re-implemented on 2026-07-28 (`e69c3ab5`) and the
corpus has been gated by `test-executable-errors` since, in both `just test` and
`ci-fast-gates`. Of its six fixtures, five are rejected with the expected diagnostic and
`missing_main` is xfail (verified 2026-08-16). The row above now describes what runs.
