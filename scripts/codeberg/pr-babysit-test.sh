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

# ---- ref_is_ancestor: the stacked-PR base-retarget trigger --------------------
# Reproduce the #314/#315 incident topology in a throwaway repo and assert the
# retarget condition fires IFF the base branch has already landed in master.
#   master @ A;  stacked-base = A + 0a;  head = stacked-base + 0b.
# PENDING (313 not merged, master still @ A): base has a commit not in master
#   -> is-ancestor FALSE -> must NOT retarget (leave it stacked).
# LANDED  (313 ff-merged, master fast-forwarded to stacked-base): base fully in
#   master -> is-ancestor TRUE -> retarget base->master.
retarget_trigger_probe() {
  local dir; dir=$(mktemp -d)
  (
    cd "$dir" || exit 9
    git init -q
    git config user.email t@t; git config user.name t
    git commit -q --allow-empty -m A
    git branch -M master
    git checkout -q -b stacked-base
    git commit -q --allow-empty -m 0a
    git checkout -q -b head-0b
    git commit -q --allow-empty -m 0b
    git checkout -q master
    ref_is_ancestor stacked-base master && echo PENDING=FIRED || echo PENDING=NOPE
    git merge -q --ff-only stacked-base            # 313 ff-merges into master
    ref_is_ancestor stacked-base master && echo LANDED=FIRED || echo LANDED=NOPE
  )
  rm -rf "$dir"
}
probe_out=$(retarget_trigger_probe)
case "$probe_out" in
  *PENDING=NOPE*) echo "PASS  pending stacked base does NOT trigger retarget"; pass=$((pass+1)) ;;
  *)              echo "FAIL  pending stacked base should NOT trigger retarget (got: $probe_out)"; fail=$((fail+1)) ;;
esac
case "$probe_out" in
  *LANDED=FIRED*) echo "PASS  landed stacked base triggers retarget"; pass=$((pass+1)) ;;
  *)              echo "FAIL  landed stacked base should trigger retarget (got: $probe_out)"; fail=$((fail+1)) ;;
esac

echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
