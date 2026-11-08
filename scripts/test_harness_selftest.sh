#!/usr/bin/env bash
# The suite runner (`just _test-stdlib`) against fixtures that must fail it, and one that must pass.
# A runner that counts a verdict-less or hung suite as passed hides every defect behind it.
# Usage: test_harness_selftest.sh <just> <stage1-binary>
set -uo pipefail

JUST="${1:?usage: test_harness_selftest.sh <just> <stage1-binary>}"
STAGE="${2:?usage: test_harness_selftest.sh <just> <stage1-binary>}"
FIX="tests/harness_selftest"
fail=0

bad() { echo "FAIL: $*" >&2; fail=1; }

# run <dir> [VAR=val ...]: output in $OUT, exit code in $RC.
run() {
  local dir="$1"; shift
  RC=0
  OUT="$(env "$@" "$JUST" _test-stdlib "$STAGE" "$FIX/$dir" 2>&1)" || RC=$?
}

run no_verdict
if [[ "$RC" -eq 0 ]]; then bad "no_verdict: runner exited 0 for a suite with no verdict"
elif ! grep -q "NO VERDICT" <<<"$OUT"; then bad "no_verdict: exit $RC but no NO VERDICT label: $OUT"; fi

run hang SPROUT_TEST_TIMEOUT=3
if [[ "$RC" -eq 0 ]]; then bad "hang: runner exited 0 for a hung suite"
elif ! grep -q "TIMEOUT" <<<"$OUT"; then bad "hang: exit $RC but no TIMEOUT label: $OUT"; fi

run pass
[[ "$RC" -eq 0 ]] || bad "pass: runner exited $RC for a passing suite: $OUT"

(( fail == 0 )) || exit 1
echo "==> harness selftest ✓"
