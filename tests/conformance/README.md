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
| `executable_error/` | ⚠️ **obsolete** — asserts entrypoint diagnostics the current compiler no longer emits | none | ❌ |
| `runtime_error/` | ⚠️ **obsolete** — same (a malformed `main` is silently accepted today) | none | ❌ |
| `parity_*/` | ⚠️ **obsolete** — byte-parity vs the retired reference compiler; no golden | none | ❌ |

## Adding a fixture

- **`run/`** — write `<name>.spr` and its expected stdout `<name>.out`. Bare `.spr`
  files are self-contained and get **no prelude** (see README §Not Yet Supported):
  define the types/constructors you use, or the fixture will fail with
  `unbound variable` / `unknown constructor`.
- **`type_error/` · `parse_error/`** — write `<name>.spr` and `<name>.err` holding
  a stable substring of the expected diagnostic (matched with `grep -F`, so pick a
  message fragment, not the line/column suffix).

## `run/XFAIL` — visible quarantine

`test-conformance-run` reads `run/XFAIL` (one basename per line, `#` comments
allowed). A listed fixture is expected to fail; if it starts **passing** again the
gate goes RED (`UNEXPECTED PASS — remove from XFAIL`), so quarantine self-heals.
An orphan `.out` with no sibling `.spr` also fails the gate. Quarantine is for
tracked, documented rot — not a place to hide new failures.

## Obsolete corpora (⚠️)

`executable_error/`, `runtime_error/`, and `parity_*/` predate the self-hosted
compiler and are **not gated**. Their disposition (delete, or re-implement
entrypoint-signature validation and regenerate goldens) is tracked in
`BACKLOG.md` §7.3. Do not treat their presence as coverage.
