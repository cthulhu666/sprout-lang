#!/usr/bin/env bash
# check_clang_cpr.sh against stub clangs that write a fixed `define` line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/check_clang_cpr.sh"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_clangcpr_test_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# stub <name> <define line>: a clang that writes the line to its -o argument.
stub() {
  cat >"$TMPD/$1" <<TOOL
#!/usr/bin/env bash
if [[ "\$1" == --version ]]; then echo "stub clang version 1.0"; exit 0; fi
while [[ \$# -gt 0 ]]; do [[ "\$1" == -o ]] && out="\$2"; shift; done
printf '%s\n' '$2' >"\$out"
TOOL
  chmod +x "$TMPD/$1"
}

stub new 'define dso_local i128 @f(i64 noundef %0) local_unnamed_addr #0 {'
stub old 'define dso_local { i64, i64 } @f(i64 noundef %0) local_unnamed_addr #0 {'
stub other 'define dso_local i128 @g(i64 noundef %0) local_unnamed_addr #0 {'

bash "$CHECK" "$TMPD/new" || fail "an i128 lowering was refused"

if bash "$CHECK" "$TMPD/old" 2>"$TMPD/old.err"; then
  fail "a { i64, i64 } lowering was accepted"
fi
grep -q 'clang >= 22' "$TMPD/old.err" || fail "the refusal does not name the fix"
grep -q '{ i64, i64 }' "$TMPD/old.err" || fail "the refusal does not show the lowering"

# An i128 elsewhere in the module must not satisfy the check.
if bash "$CHECK" "$TMPD/other" 2>/dev/null; then
  fail "a define of another function was accepted"
fi

echo "==> check_clang_cpr tests passed"
