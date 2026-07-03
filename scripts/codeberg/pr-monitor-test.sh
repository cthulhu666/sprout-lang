#!/usr/bin/env bash
# Regression test for ci_from_tasks (scripts/codeberg/_lib.sh), the CI-signal
# aggregator used by pr-monitor.sh. Pure/offline — no config or network.
#
# Guards the 2026-07-03 bug where the monitor reported ci=success in the
# `setup`-green / `test`-not-spawned window and merged PR#121 prematurely.
#
# Run:  scripts/codeberg/pr-monitor-test.sh   (exit 0 = all pass)

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
CODEBERG_LIB_NO_SETUP=1 source "$SCRIPT_DIR/_lib.sh"

SHA=abc1234567
pass=0 fail=0
check() {  # <name> <tasks-json> <expected>
  local name=$1 json=$2 want=$3 got
  got=$(ci_from_tasks "$json" "$SHA")
  if [ "$got" = "$want" ]; then echo "PASS  $name -> $got"; pass=$((pass+1))
  else echo "FAIL  $name -> got=$got want=$want"; fail=$((fail+1)); fi
}

# THE BUG: setup green, test not spawned yet -> must be no-status, NOT success.
check "setup-only (regression guard)" \
  '{"workflow_runs":[{"head_sha":"abc1234567","name":"setup","status":"success"},{"head_sha":"abc1234567","name":"setup","status":"success"}]}' \
  no-status
check "test running" \
  '{"workflow_runs":[{"head_sha":"abc1234567","name":"setup","status":"success"},{"head_sha":"abc1234567","name":"test","status":"running"}]}' \
  pending
check "both test runs green" \
  '{"workflow_runs":[{"head_sha":"abc1234567","name":"test","status":"success"},{"head_sha":"abc1234567","name":"test","status":"success"}]}' \
  success
check "one test failed (other green)" \
  '{"workflow_runs":[{"head_sha":"abc1234567","name":"test","status":"failure"},{"head_sha":"abc1234567","name":"test","status":"success"}]}' \
  failure
check "only a cancelled/superseded test run" \
  '{"workflow_runs":[{"head_sha":"abc1234567","name":"test","status":"cancelled"}]}' \
  no-status
check "different sha ignored" \
  '{"workflow_runs":[{"head_sha":"deadbeef99","name":"test","status":"success"}]}' \
  no-status
check "forgejo .tasks key fallback" \
  '{"tasks":[{"head_sha":"abc1234567","name":"test","status":"success"}]}' \
  success

echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
