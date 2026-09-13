#!/usr/bin/env bash
# Tests for scripts/review_ledger.sh. Runs against throwaway repos, never the
# caller's, so it can be run from anywhere without touching real ledger state.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LEDGER="$ROOT/scripts/review_ledger.sh"

fail=0
check() {
  local what="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    printf '  ok   %s\n' "$what"
  else
    printf '  FAIL %s: wanted [%s], got [%s]\n' "$what" "$want" "$got" >&2
    fail=1
  fi
}

R=$(mktemp -d /tmp/sprout_ledger_test_XXXXXX)
trap 'rm -rf "$R"' EXIT
cd "$R" || exit 1
git init -q -b main .
git config user.email t@t
git config user.name t
echo a > a.txt
git add a.txt
git commit -qm first

check "no runs yet counts zero"        "0"  "$(bash "$LEDGER" count)"
check "no runs yet shows nothing"      ""   "$(bash "$LEDGER" show)"

id1=$(bash "$LEDGER" open)
check "an open run is not yet complete" "0" "$(bash "$LEDGER" count)"

bash "$LEDGER" done "$id1" 7 3
check "a closed run counts once"       "1"  "$(bash "$LEDGER" count)"
check "show reports counts"            "review:1 7 found 3 real" "$(bash "$LEDGER" show)"

# A retry that re-reports the same run must not inflate the count — the id
# column is what dedupes, and this is the assertion that pins it.
bash "$LEDGER" done "$id1" 7 3
check "re-reporting one run stays 1"   "1"  "$(bash "$LEDGER" count)"

id2=$(bash "$LEDGER" open)
bash "$LEDGER" done "$id2" 2 0
check "a second run counts twice"      "2"  "$(bash "$LEDGER" count)"
check "counts accumulate across runs"  "review:2 9 found 3 real" "$(bash "$LEDGER" show)"

# Per-branch isolation is the whole point: the count answers "has THIS PR been
# reviewed", so another branch's runs must not leak in.
git switch -q -c other
check "a fresh branch starts at zero"  "0"  "$(bash "$LEDGER" count)"
id3=$(bash "$LEDGER" open)
bash "$LEDGER" done "$id3" 1 1
check "the new branch counts its own"  "1"  "$(bash "$LEDGER" count)"
git switch -q main
check "the original branch is intact"  "2"  "$(bash "$LEDGER" count)"

# Outside a repository the ledger must stay quiet rather than erroring: the
# status line calls `show` on every render, wherever the user happens to be.
cd /tmp || exit 1
check "outside a repo, count is zero"  "0"  "$(bash "$LEDGER" count)"
check "outside a repo, show is silent" ""   "$(bash "$LEDGER" show 2>/dev/null)"

if [ "$fail" -ne 0 ]; then
  echo "==> review-ledger tests FAILED" >&2
  exit 1
fi
echo "==> review-ledger tests passed"
