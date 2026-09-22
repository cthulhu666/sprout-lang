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

# A session can start anywhere in the tree, and a ledger that silently writes
# nowhere reads exactly like a branch that was never reviewed — the one thing
# this ledger exists to report. So it must resolve the same file from a
# subdirectory, not just from the root.
mkdir -p sub/deeper
cd sub/deeper || exit 1
check "counts from a subdirectory"     "2"  "$(bash "$LEDGER" count)"
id4=$(bash "$LEDGER" open)
bash "$LEDGER" done "$id4" 1 1
check "a run recorded from a subdir"   "3"  "$(bash "$LEDGER" count)"
cd "$R" || exit 1
check "and the root sees that run too" "3"  "$(bash "$LEDGER" count)"

# The findings file is what makes step 4 checkable. A review whose findings live
# only in a chat message cannot be shown to anyone later, and the skill's own
# argument for a ledger — "a review you cannot point at did not happen" — applies
# to the findings just as much as to the count.
cd "$R" || exit 1
fpath=$(bash "$LEDGER" findings "$id1")
check "findings path names the run"    "1"  "$(printf '%s' "$fpath" | grep -c "findings-$id1.md$")"
check "findings path is under GIT_DIR" "1"  "$(printf '%s' "$fpath" | grep -c '/claude-review/')"
# `dirname ""` is `.`, which always exists — so assert the directory this command
# is supposed to create, by name, or the check passes on a command that does not
# exist yet.
check "findings dir was created"       "1"  "$([ -n "$fpath" ] && [ -d "${fpath%/*}" ] && echo 1 || echo 0)"

# Writing the file must not disturb the counts: the ledger is append-only TSV and
# the findings are a sibling file, not a column.
echo "# findings" > "$fpath"
check "writing findings keeps count"   "3"  "$(bash "$LEDGER" count)"

# A run id reaches this from a workflow result, so it is not trusted to be a bare
# token. `../../etc/passwd` must not resolve to a path outside the ledger dir.
check "a traversing id is rejected"     "1" \
  "$(bash "$LEDGER" findings "../../escape" 2>&1 >/dev/null | grep -c 'run id')"
check "a missing id is rejected"       "1" \
  "$(bash "$LEDGER" findings 2>&1 >/dev/null | grep -c 'run id')"

# Same subdirectory rule as the rest: a session can start anywhere.
mkdir -p sub2
cd sub2 || exit 1
sub_path=$(bash "$LEDGER" findings "$id1")
check "findings path from a subdir"    "1"  "$([ -n "$sub_path" ] && [ "$sub_path" = "$fpath" ] && echo 1 || echo 0)"
cd "$R" || exit 1

# --- the effort column ---------------------------------------------------
# `rv:3` cannot distinguish three `low` reviews from three `max` ones, so the
# level a run was asked for is recorded as an eighth column. Everything below
# pins that it is additive: every existing reader selects $2/$3/$5/$6/$7, and a
# row written before the column existed has only seven fields.
git switch -q -c effort-col
check "effort branch starts at zero"   "0"  "$(bash "$LEDGER" count)"

ide=$(bash "$LEDGER" open)
bash "$LEDGER" done "$ide" 4 2 high
check "show reports the level"         "review:1 4 found 2 real @high" "$(bash "$LEDGER" show)"

# The level is the LATEST completed run's, not an aggregate: no question is
# answered by summing levels across runs, and "how hard was the last look" is.
ide2=$(bash "$LEDGER" open)
bash "$LEDGER" done "$ide2" 9 1 max
check "the latest run's level wins"    "review:2 13 found 3 real @max" "$(bash "$LEDGER" show)"

# Re-reporting must not move anything, the level included: only a run's FIRST
# `done` row counts, so a retry naming a different level is ignored.
bash "$LEDGER" done "$ide2" 9 1 low
check "a retry does not move the level" "review:2 13 found 3 real @max" "$(bash "$LEDGER" show)"
check "a retry does not inflate count"  "2" "$(bash "$LEDGER" count)"

# Omitting the level is how every pre-existing caller behaves, and must leave
# `show` in its old shape rather than printing an empty marker.
git switch -q -c effort-absent
ida=$(bash "$LEDGER" open)
bash "$LEDGER" done "$ida" 5 5
check "an omitted level prints nothing" "review:1 5 found 5 real" "$(bash "$LEDGER" show)"

# A seven-field row is what the ledger held before this column existed. Such a
# row must keep counting and reporting, or the change silently erases history.
runs="$(git rev-parse --absolute-git-dir)/claude-review/runs.tsv"
printf '2020-01-01T00:00:00Z\tdone\teffort-absent\tdeadbee\t999-1\t3\t1\n' >> "$runs"
check "a legacy 7-field row counts"    "2"  "$(bash "$LEDGER" count)"
check "a legacy row keeps show intact" "review:2 8 found 6 real" "$(bash "$LEDGER" show)"

# The level reaches this from a parsed argument, so an unknown token is dropped
# rather than stored: a tab in it would split the row and corrupt every reader.
git switch -q -c effort-bogus
idb=$(bash "$LEDGER" open)
bash "$LEDGER" done "$idb" 1 1 "$(printf 'hi\tgh')"
check "a bogus level is not recorded"  "review:1 1 found 1 real" "$(bash "$LEDGER" show)"
check "a bogus level keeps 8 columns"  "1" \
  "$(awk -F'\t' '$2=="done" && $3=="effort-bogus" { print NF }' "$runs" | grep -c '^8$')"
git switch -q main

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
