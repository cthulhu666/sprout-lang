#!/usr/bin/env bash
# Regression tests for the pure decision helpers used by pr-babysit.sh (the
# end-to-end merge-queue orchestrator). Pure/offline — no config or network.
#
# Run:  scripts/codeberg/pr-babysit-test.sh   (exit 0 = all pass)

set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
CODEBERG_LIB_NO_SETUP=1 source "$SCRIPT_DIR/_lib.sh"

pass=0 fail=0
ok() {   # <name> <http> : is_merge_success should SUCCEED
  if is_merge_success "$2"; then echo "PASS  $1"; pass=$((pass+1))
  else echo "FAIL  $1 (is_merge_success $2 should be true)"; fail=$((fail+1)); fi
}
no() {   # <name> <http> : is_merge_success should FAIL
  if is_merge_success "$2"; then echo "FAIL  $1 (is_merge_success $2 should be false)"; fail=$((fail+1))
  else echo "PASS  $1"; pass=$((pass+1)); fi
}

ok  "200 is a merge"                 200
ok  "201 is a merge"                 201
no  "409 is not a merge (needs ff diagnosis)" 409
no  "405 is not a merge"             405
# THE BUG being guarded: 500 is what Gitea returns for a non-fast-forwardable
# ff-only merge. It must read as 'not merged' (→ diagnose ff-ability), NOT as a
# fatal/unknown error that escalates. (PR#271, 2026-07-27.)
no  "500 is not a merge (Gitea non-ff signature — must NOT escalate)" 500
no  "empty status is not a merge"    ""
no  "000 (curl connect fail) is not a merge" 000

echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
