#!/usr/bin/env bash
# Gate: second-root (`--package-root`) module resolution.
#
#   Positive: an app importing a module from an extra package root type-checks
#             cleanly when that root is registered with --package-root.
#   Negative: the same import with NO --package-root must fail to resolve
#             (guards against accidentally widening the default search path).
#
# Uses `--phase check` (resolve + typecheck) — proving the import resolves is the
# unit under test; end-to-end compile+run of a cross-package program is covered by
# the http_pg_server demo in the sprout-postgres repo.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRV="$ROOT/build/compile_driver_bin_stage1"
STDLIB="$ROOT/stdlib"
FIX="$ROOT/tests/conformance/package_resolution"
PKG_ROOT="$FIX/roots"
APP="$FIX/app.spr"
fail=0

# Real diagnostics are `<pos>ERROR: ...` or `Unknown variable ...`; the check phase
# also dumps the type env (names like `result_map_error`), so match error markers
# precisely (case-sensitive `ERROR:` / `Unknown variable`), not a bare `error`.
errors() { echo "$1" | grep -E 'ERROR:|Unknown variable'; }

# Positive: registered extra root -> import resolves, check reports OK, no errors.
pos="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$APP" 2>&1)"
if echo "$pos" | grep -q '^OK$' && ! errors "$pos" >/dev/null; then
  echo "PASS positive: second-root import resolved and type-checked"
else
  echo "FAIL positive: --package-root did not resolve demo.greet"
  errors "$pos" | head -5
  fail=1
fi

# Negative: no extra root -> demo.greet is unresolvable (unknown variable).
neg="$("$DRV" --phase check "$STDLIB" "$APP" 2>&1)"
if errors "$neg" >/dev/null; then
  echo "PASS negative: unregistered dotted import correctly failed to resolve"
else
  echo "FAIL negative: demo.greet resolved WITHOUT --package-root (search path too wide)"
  fail=1
fi

# Header check: an imported file must declare a `module` header
# (docs/prelude-scope-v0.md §4.2 step 3). The root IS registered here, so
# resolution succeeds and the header check is the only thing that can reject it.
# Asserted on the MESSAGE, not just the exit status: the whole point of the
# diagnostic is that it names the file and the cause, where the old behaviour
# reported `... is defined more than once in this module` about whichever symbol
# happened to collide with the prelude — or nothing at all.
hdr="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_headerless.spr" 2>&1)"
if echo "$hdr" | grep -q 'no `module` header' && echo "$hdr" | grep -q 'headerless.sprout'; then
  echo "PASS header: headerless imported file diagnosed by path and cause"
else
  echo "FAIL header: expected a 'no \`module\` header' diagnostic naming headerless.sprout"
  echo "$hdr" | head -5
  fail=1
fi

[ "$fail" -eq 0 ] && echo "==> package-resolution gate: OK" || echo "==> package-resolution gate: FAILED"
exit $fail
