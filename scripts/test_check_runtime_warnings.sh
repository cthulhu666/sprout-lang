#!/usr/bin/env bash
# check_runtime_warnings.sh against fixtures: -Werror applies only on the pinned clang major.
set -uo pipefail
cd "$(dirname "$0")/.."
SCRIPT=scripts/check_runtime_warnings.sh
CLEAN=tests/runtime_warnings/clean.c
WARN=tests/runtime_warnings/unused_var.c
LOCAL=$(bash "$SCRIPT" --print-local-major)
OTHER=$((LOCAL + 1))
fail=0
check() { # name want-exit got-exit
  if [[ "$2" != "$3" ]]; then echo "FAIL: $1 (exit $3, want $2)"; fail=1; else echo "ok: $1"; fi
}

out=$(bash "$SCRIPT" --pinned-major "$LOCAL" "$WARN" 2>&1); rc=$?
check "warning + pin = local fails" 1 "$rc"
grep -q "unused variable" <<<"$out" || { echo "FAIL: warning text not printed"; fail=1; }

out=$(bash "$SCRIPT" --pinned-major "$LOCAL" "$CLEAN" 2>&1); rc=$?
check "clean + pin = local passes" 0 "$rc"

out=$(env -u CI bash "$SCRIPT" --pinned-major "$OTHER" "$WARN" 2>&1); rc=$?
check "warning + pin != local passes" 0 "$rc"
grep -q "not enforced" <<<"$out" || { echo "FAIL: 'not enforced' line missing"; fail=1; }

out=$(CI=true bash "$SCRIPT" --pinned-major "$OTHER" "$CLEAN" 2>&1); rc=$?
check "pin != local under CI fails" 1 "$rc"

# The real runtime, default args: enforced on the pinned major, warn-only elsewhere.
bash "$SCRIPT"; rc=$?
check "real runtime" 0 "$rc"

exit "$fail"
