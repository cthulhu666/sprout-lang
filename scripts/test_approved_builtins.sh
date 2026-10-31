#!/usr/bin/env bash
# check_approved_builtins.sh against the fixtures in tests/approved_builtins/.
#
# The fixture runtime defines a `_Bool` builtin, a pointer-returning builtin, a noreturn `void`
# builtin, a non-static function no Sprout code names, and a static one. None but `fx_plain` is
# a `long long`, so the old `^long long` grep saw no builtin that the new set requires.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/check_approved_builtins.sh"
FX="$ROOT/tests/approved_builtins"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# run <allowlist> [flag]...: output in $OUT, exit code in $RC.
run() {
  local allow="$1"; shift
  RC=0
  OUT="$(bash "$CHECK" --allowlist "$FX/$allow" --ir "$FX/header.ll" --extern-root "$FX" "$@" \
    -- "$FX/runtime.c" 2>&1)" || RC=$?
}

# expect_fail <case> <needle>...: the last run exited 1 and printed every needle.
expect_fail() {
  local case_name="$1"; shift
  [[ "$RC" == 1 ]] || fail "$case_name: exit $RC, want 1: $OUT"
  for needle in "$@"; do
    grep -qF -- "$needle" <<<"$OUT" || fail "$case_name: output lacks '$needle': $OUT"
  done
  echo "ok: $case_name"
}

# expect_absent <case> <needle>...: the last run's output mentions none of them.
expect_absent() {
  local case_name="$1"; shift
  for needle in "$@"; do
    if grep -qwF -- "$needle" <<<"$OUT"; then fail "$case_name: output mentions '$needle': $OUT"; fi
  done
  echo "ok: $case_name"
}

run allow_missing.txt
expect_fail "(1) _Bool and pointer-returning builtins missing from the allowlist" \
  "fx_flag" "fx_pair" "missing from"
expect_absent "(1b) the noreturn and long long builtins listed are not reported" fx_plain fx_abort
expect_absent "(3) a non-static function no Sprout code names is not required" \
  fx_internal fx_use_static fx_static fx_not_in_runtime llvm.memset.p0.i64

run allow_stale.txt
expect_fail "(2) allowlist entries that are not builtins are reported" \
  "fx_ghost" "fx_internal" "not builtins"
expect_absent "(2b) real builtins are not reported stale" fx_flag fx_pair fx_plain fx_abort

run allow_complete.txt
[[ "$RC" == 0 ]] || fail "(4) complete allowlist: exit $RC: $OUT"
grep -qF "check-approved-builtins ✓" <<<"$OUT" || fail "(4) no success line: $OUT"
echo "ok: (4) complete allowlist passes"

# An --exclude dir hides its externs from the scan.
run allow_missing.txt --exclude "$FX"
[[ "$RC" == 1 ]] && grep -qF "not builtins" <<<"$OUT" && ! grep -qw fx_flag <<<"$OUT" \
  || fail "(5) --exclude: expected fx_plain stale only: exit $RC: $OUT"
grep -qw fx_plain <<<"$OUT" || fail "(5) --exclude: fx_plain not reported stale: $OUT"
echo "ok: (5) --exclude hides externs"

echo "test-approved-builtins: ok"
