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

# The RAW findings are a second sibling, and the only record of what each pass said
# in its own words. Keeping them is what made OVERLAP_MIN measurable, and measuring
# it is what retired it — 0.5 had survived three runs on adjectives until then.
rpath=$(bash "$LEDGER" raw "$id1")
check "raw path names the run"         "1"  "$(printf '%s' "$rpath" | grep -c "raw-$id1.json$")"
check "raw path is under GIT_DIR"      "1"  "$(printf '%s' "$rpath" | grep -c '/claude-review/')"
check "raw is not the findings file"   "1"  "$([ "$rpath" != "$fpath" ] && echo 1 || echo 0)"
echo '[]' > "$rpath"
check "writing raw keeps count"        "3"  "$(bash "$LEDGER" count)"
check "a traversing raw id is rejected" "1" \
  "$(bash "$LEDGER" raw "../../escape" 2>&1 >/dev/null | grep -c 'run id')"
check "a missing raw id is rejected"   "1" \
  "$(bash "$LEDGER" raw 2>&1 >/dev/null | grep -c 'run id')"

# `--write` lets the script do the write: in a worktree the ledger dir sits in the
# main checkout's `.git`, which an isolated session's file tools refuse.
wid="write-test"
printf '# findings\n\nline two\n  indented\n' > "$R/want.md"
wpath=$(bash "$LEDGER" findings "$wid" --write < "$R/want.md")
check "findings --write prints the plain path" "$(bash "$LEDGER" findings "$wid")" "$wpath"
check "findings --write round-trips"   "0"  "$(cmp -s "$R/want.md" "$wpath"; echo $?)"
printf '[]' > "$R/want.json"
wraw=$(bash "$LEDGER" raw "$wid" --write < "$R/want.json")
check "raw --write prints the plain path" "$(bash "$LEDGER" raw "$wid")" "$wraw"
check "raw --write round-trips []"     "0"  "$(cmp -s "$R/want.json" "$wraw"; echo $?)"
check "--write leaves no temp file"    "2"  "$(ls "${wpath%/*}" | grep -c -- "-$wid\.")"

bash "$LEDGER" findings "$wid" --write < /dev/null 2> "$R/err.txt"
check "empty stdin exits 2"            "2"  "$?"
check "empty stdin says so"            "1"  "$(grep -c 'empty' "$R/err.txt")"
check "empty stdin keeps the old file" "0"  "$(cmp -s "$R/want.md" "$wpath"; echo $?)"
bash "$LEDGER" findings "fresh-empty" --write < /dev/null 2> /dev/null
check "empty stdin creates no file"    "0"  "$(ls "${wpath%/*}" | grep -c 'fresh-empty')"

printf 'x' | bash "$LEDGER" findings "../../escape" --write 2> "$R/err.txt"
check "a traversing id with --write is rejected" "2" "$?"
check "a traversing id writes nothing" "0"  "$(find "$R" -name 'escape*' | wc -l | tr -d ' ')"
printf 'x' | bash "$LEDGER" findings "$wid" --bogus 2> /dev/null
check "an unknown flag is rejected"    "2"  "$?"
check "an unknown flag writes nothing" "0"  "$(cmp -s "$R/want.md" "$wpath"; echo $?)"

# Same subdirectory rule as the rest: a session can start anywhere.
mkdir -p sub2
cd sub2 || exit 1
printf 'from sub\n' | bash "$LEDGER" findings "sub-write" --write > /dev/null
check "--write from a subdir"          "from sub" "$(cat "${wpath%/*}/findings-sub-write.md")"
sub_path=$(bash "$LEDGER" findings "$id1")
check "findings path from a subdir"    "1"  "$([ -n "$sub_path" ] && [ "$sub_path" = "$fpath" ] && echo 1 || echo 0)"
sub_raw=$(bash "$LEDGER" raw "$id1")
check "raw path from a subdir"         "1"  "$([ -n "$sub_raw" ] && [ "$sub_raw" = "$rpath" ] && echo 1 || echo 0)"
cd "$R" || exit 1

# --- the effort column ---------------------------------------------------
# A count of 3 cannot distinguish three `low` reviews from three `max` ones, so the
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
check "a bogus level keeps 11 columns" "1" \
  "$(awk -F'\t' '$2=="done" && $3=="effort-bogus" { print NF }' "$runs" | grep -c '^11$')"

# --- the cleanups column -------------------------------------------------
# Cleanups are not bugs, so they get their own ninth column rather than a share
# of `found`, which means "bug reports" in every row ever written.
git switch -q -c cleanup-col
idc=$(bash "$LEDGER" open)
bash "$LEDGER" done "$idc" 4 2 high 6
check "the cleanup count is column 9"  "6" \
  "$(awk -F'\t' '$2=="done" && $3=="cleanup-col" { print $9 }' "$runs")"
check "cleanups leave show unchanged"  "review:1 4 found 2 real @high" "$(bash "$LEDGER" show)"
check "a start row is 11 columns wide" "11" \
  "$(awk -F'\t' '$2=="start" && $3=="cleanup-col" { print NF }' "$runs")"
# Same rule as the level: only a count is stored, never a token that could
# carry a tab into the row.
idd=$(bash "$LEDGER" open)
bash "$LEDGER" done "$idd" 1 1 high "$(printf '3\tx')"
check "a non-numeric cleanup count is dropped" "-" \
  "$(awk -F'\t' -v i="$idd" '$2=="done" && $5==i { print $9 }' "$runs")"
# An 8-field row predates the column and must keep counting.
printf '2020-01-01T00:00:00Z\tdone\tcleanup-col\tdeadbee\t999-2\t3\t1\tlow\n' >> "$runs"
check "a legacy 8-field row counts"    "3"  "$(bash "$LEDGER" count)"

# --- the confirmed-cleanups column ----------------------------------------
# Cleanups have their own skeptic, so how many survived it is a tenth column.
# Same rules as the ninth: digits only, and older rows keep counting.
git switch -q -c cleanup-real
ide=$(bash "$LEDGER" open)
bash "$LEDGER" done "$ide" 4 2 high 8 5
check "confirmed cleanups are column 10" "5" \
  "$(awk -F'\t' -v i="$ide" '$2=="done" && $5==i { print $10 }' "$runs")"
check "confirmed cleanups leave show unchanged" "review:1 4 found 2 real @high" \
  "$(bash "$LEDGER" show)"
idf=$(bash "$LEDGER" open)
bash "$LEDGER" done "$idf" 1 1 high 3 "$(printf '2\tx')"
check "a non-numeric confirmed count is dropped" "-" \
  "$(awk -F'\t' -v i="$idf" '$2=="done" && $5==i { print $10 }' "$runs")"
idg=$(bash "$LEDGER" open)
bash "$LEDGER" done "$idg" 1 1 high 3
check "an omitted confirmed count is '-'" "-" \
  "$(awk -F'\t' -v i="$idg" '$2=="done" && $5==i { print $10 }' "$runs")"
# A 9-field row predates the column and must keep counting.
printf '2020-01-01T00:00:00Z\tdone\tcleanup-real\tdeadbee\t999-3\t3\t1\tlow\t2\n' >> "$runs"
check "a legacy 9-field row counts"    "4"  "$(bash "$LEDGER" count)"
git switch -q main

# --- the reviewed target --------------------------------------------------
# A review of PR 779 run while another branch is checked out used to be filed under
# the checked-out branch. `open --pr` records the PR's branch and head instead, in
# an eleventh column `target`; `done` copies all three from the start row.
mkdir -p "$R/stub"
cat > "$R/stub/gh" <<'EOF'
#!/usr/bin/env bash
if [ "${STUB_GH_FAIL:-}" = 1 ]; then echo "gh: boom" >&2; exit 1; fi
printf '%s\n' "$*" > "$STUB_GH_ARGS"
# With --jq the real gh prints the selected values, one per line: branch, head oid,
# cross-repo flag, head repository owner.
printf '%s\n%s\n%s\n%s\n' "${STUB_GH_BRANCH:-pr-branch}" \
  "${STUB_GH_OID:-0123456789abcdef0123456789abcdef01234567}" \
  "${STUB_GH_CROSS:-false}" "${STUB_GH_OWNER-owner}"
EOF
chmod +x "$R/stub/gh"
export STUB_GH_ARGS="$R/gh_args.txt"
rows() { wc -l < "$runs" | tr -d ' '; }
start_of() { awk -F'\t' -v i="$1" '$2=="start" && $5==i' "$runs"; }
done_of() { awk -F'\t' -v i="$1" '$2=="done" && $5==i' "$runs"; }
field() { printf '%s' "$2" | awk -F'\t' -v n="$1" '{ print $n }'; }

idp=$(PATH="$R/stub:$PATH" bash "$LEDGER" open --pr 779)
rowp=$(start_of "$idp")
check "open --pr asks gh for the PR"    "1" \
  "$(grep -c '^pr view 779 --json headRefName,headRefOid,isCrossRepository,headRepositoryOwner --jq ' \
     "$STUB_GH_ARGS")"
check "open --pr records the PR branch" "pr-branch" "$(field 3 "$rowp")"
check "open --pr records the PR head"   "01234567"  "$(field 4 "$rowp")"
check "open --pr records the target"    "pr:779"    "$(field 11 "$rowp")"
check "open --pr is not the checked-out branch" "main" "$(git rev-parse --abbrev-ref HEAD)"
check "a start row is 11 wide with --pr" "11" "$(printf '%s' "$rowp" | awk -F'\t' '{ print NF }')"

# HEAD moves between open and done; the done row must still name the reviewed code.
git switch -q -c mover
echo b > b.txt
git add b.txt
git commit -qm second
bash "$LEDGER" done "$idp" 2 1 medium
rowd=$(done_of "$idp")
check "done copies the branch"          "pr-branch" "$(field 3 "$rowd")"
check "done copies the head"            "01234567"  "$(field 4 "$rowd")"
check "done copies the target"          "pr:779"    "$(field 11 "$rowd")"
check "done row is 11 wide"             "11"        "$(printf '%s' "$rowd" | awk -F'\t' '{ print NF }')"
check "count --branch sees the PR run"  "1"  "$(bash "$LEDGER" count --branch pr-branch)"
check "the checked-out branch is not credited" "0" "$(bash "$LEDGER" count)"
check "count --branch of an unknown name is 0" "0" "$(bash "$LEDGER" count --branch nosuch)"
check "count --branch matches plain count" "$(bash "$LEDGER" count)" \
  "$(bash "$LEDGER" count --branch mover)"
bash "$LEDGER" count --branch > /dev/null 2>&1
check "count --branch needs a name"     "2"  "$?"
bash "$LEDGER" count --bogus > /dev/null 2>&1
check "count rejects an unknown flag"   "2"  "$?"

# gh failing must not fall back to the checked-out branch.
before=$(rows)
PATH="$R/stub:$PATH" STUB_GH_FAIL=1 bash "$LEDGER" open --pr 780 > /dev/null 2> "$R/err.txt"
check "gh failing exits non-zero"       "1"  "$?"
check "gh failing names the problem"    "1"  "$(grep -c 'no row written' "$R/err.txt")"
check "gh failing writes no row"        "$before" "$(rows)"
bash "$LEDGER" open --pr 12x > /dev/null 2>&1
check "a non-numeric PR is rejected"    "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
bash "$LEDGER" open --pr > /dev/null 2>&1
check "a missing PR number is rejected" "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
PATH="$R/stub:$PATH" STUB_GH_OID=nothex bash "$LEDGER" open --pr 781 > /dev/null 2>&1
check "a non-hex head from gh is rejected" "1" "$([ $? -ne 0 ] && echo 1 || echo 0)"
check "rejected opens write no row"     "$before" "$(rows)"

# --branch names the reviewed branch without asking gh.
git switch -q main
git switch -q -c feature
echo c > c.txt
git add c.txt
git commit -qm third
git switch -q main
idbr=$(bash "$LEDGER" open --branch feature)
rowb=$(start_of "$idbr")
check "open --branch records the branch" "feature" "$(field 3 "$rowb")"
check "open --branch records its head"   "$(git rev-parse --short feature)" "$(field 4 "$rowb")"
check "open --branch records the target" "branch:feature" "$(field 11 "$rowb")"
bash "$LEDGER" done "$idbr" 1 1
check "count --branch sees a branch run" "1" "$(bash "$LEDGER" count --branch feature)"
before=$(rows)
bash "$LEDGER" open --branch nosuch > /dev/null 2>&1
check "an unknown branch is rejected"   "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
bash "$LEDGER" open --branch "$(printf 'a\tb')" > /dev/null 2>&1
check "a tab in a branch is rejected"   "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
bash "$LEDGER" open --branch -x > /dev/null 2>&1
check "a dash-led branch is rejected"   "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
bash "$LEDGER" open --bogus > /dev/null 2>&1
check "open rejects an unknown flag"    "2"  "$?"
check "rejected opens write no row (branch)" "$before" "$(rows)"

# A remote-only branch resolves through origin; the bare name is what gets recorded.
git update-ref refs/remotes/origin/remoteonly "$(git rev-parse HEAD)"
idr=$(bash "$LEDGER" open --branch remoteonly)
check "open --branch falls back to origin" "remoteonly" "$(field 3 "$(start_of "$idr")")"
check "open --branch origin head"       "$(git rev-parse --short HEAD)" \
  "$(field 4 "$(start_of "$idr")")"
check "open --branch origin target"     "branch:remoteonly" "$(field 11 "$(start_of "$idr")")"
bash "$LEDGER" done "$idr" 1 1
check "count --branch finds the bare name" "1" "$(bash "$LEDGER" count --branch remoteonly)"
# A local branch wins over origin's, and a tag or bare sha is not a branch.
git update-ref refs/remotes/origin/feature "$(git rev-parse main)"
check "a local branch beats origin's"   "$(git rev-parse --short feature)" \
  "$(field 4 "$(start_of "$(bash "$LEDGER" open --branch feature)")")"
git tag atag
bash "$LEDGER" open --branch atag > /dev/null 2>&1
check "a tag is not a branch"           "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
bash "$LEDGER" open --branch nosuch > /dev/null 2> "$R/err.txt"
check "neither ref: rejected"           "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
check "neither ref: says so"            "1"  "$(grep -c "no such branch 'nosuch'" "$R/err.txt")"

# A PR head that exists locally is abbreviated like every other row; one that does not
# keeps the 8-char slice.
here=$(git rev-parse HEAD)
idh=$(PATH="$R/stub:$PATH" STUB_GH_OID="$here" bash "$LEDGER" open --pr 782)
check "a local PR head uses rev-parse --short" "$(git rev-parse --short HEAD)" \
  "$(field 4 "$(start_of "$idh")")"
idh2=$(PATH="$R/stub:$PATH" STUB_GH_OID="$here" bash "$LEDGER" open --pr 782)
check "and not the 8-char slice"        "1" \
  "$([ "$(field 4 "$(start_of "$idh2")")" != "${here:0:8}" ] && echo 1 || echo 0)"
idh3=$(PATH="$R/stub:$PATH" STUB_GH_OID=abcdef0123456789 bash "$LEDGER" open --pr 782)
check "an unknown PR head keeps 8 chars" "abcdef01" "$(field 4 "$(start_of "$idh3")")"

# A fork PR is filed as <owner>:<branch>, so it cannot collide with a local branch of
# the same name; a same-repo PR is unchanged.
idk=$(PATH="$R/stub:$PATH" STUB_GH_CROSS=true STUB_GH_OWNER=alice STUB_GH_BRANCH=fix \
      bash "$LEDGER" open --pr 783)
check "a fork PR is filed as owner:branch" "alice:fix" "$(field 3 "$(start_of "$idk")")"
check "a fork PR keeps its target"      "pr:783" "$(field 11 "$(start_of "$idk")")"
bash "$LEDGER" done "$idk" 1 1
check "a fork PR done keeps the name"   "alice:fix" "$(field 3 "$(done_of "$idk")")"
check "count --branch owner:branch"     "1"  "$(bash "$LEDGER" count --branch alice:fix)"
check "a fork PR does not credit the local branch" "0" "$(bash "$LEDGER" count --branch fix)"
idk2=$(PATH="$R/stub:$PATH" STUB_GH_CROSS=false STUB_GH_OWNER=alice STUB_GH_BRANCH=fix \
       bash "$LEDGER" open --pr 784)
check "a same-repo PR is not prefixed"  "fix" "$(field 3 "$(start_of "$idk2")")"
before=$(rows)
for bad_owner in "$(printf 'a\tb')" "-x" ""; do
  PATH="$R/stub:$PATH" STUB_GH_CROSS=true STUB_GH_OWNER="$bad_owner" \
    bash "$LEDGER" open --pr 785 > /dev/null 2>&1
  rc=$?
  check "a bad fork owner [$(printf '%s' "$bad_owner" | tr '\t' T)] is rejected" "1" \
    "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
done
PATH="$R/stub:$PATH" STUB_GH_CROSS=maybe bash "$LEDGER" open --pr 785 > /dev/null 2>&1
check "a bad cross-repo flag is rejected" "1" "$([ $? -ne 0 ] && echo 1 || echo 0)"
check "rejected fork opens write no row" "$before" "$(rows)"

# No flag behaves as before and records no target.
idn=$(bash "$LEDGER" open)
check "a plain open has target '-'"     "-"  "$(field 11 "$(start_of "$idn")")"
check "a plain open records HEAD's branch" "main" "$(field 3 "$(start_of "$idn")")"

# done needs a start row: with none there is nothing to copy, and recomputing from HEAD
# is the misattribution this fixes.
before=$(rows)
bash "$LEDGER" done "no-such-run" 1 1 > /dev/null 2> "$R/err.txt"
check "done of an unknown id fails"     "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
check "done of an unknown id says so"   "1"  "$(grep -c 'no-such-run' "$R/err.txt")"
check "done of an unknown id writes no row" "$before" "$(rows)"

# Rows written before the target column: 7, 9 and 10 fields all keep counting.
git switch -q -c legacy-widths
printf '2020-01-01T00:00:00Z\tdone\tlegacy-widths\tdeadbee\tL-1\t3\t1\n' >> "$runs"
printf '2020-01-01T00:00:00Z\tdone\tlegacy-widths\tdeadbee\tL-2\t2\t2\thigh\t4\n' >> "$runs"
printf '2020-01-01T00:00:00Z\tdone\tlegacy-widths\tdeadbee\tL-3\t1\t0\tlow\t2\t1\n' >> "$runs"
check "legacy 7-, 9- and 10-field rows count" "3" "$(bash "$LEDGER" count)"
check "legacy rows keep show intact"    "review:3 6 found 3 real @low" "$(bash "$LEDGER" show)"
check "count --branch reads legacy rows" "3" "$(bash "$LEDGER" count --branch legacy-widths)"
# A legacy start row has no target; done copies "-".
printf '2020-01-01T00:00:00Z\tstart\told-branch\tabc1234\tL-4\t-\t-\t-\t-\t-\n' >> "$runs"
bash "$LEDGER" done L-4 1 1
rowl=$(done_of L-4)
check "done from a legacy start: branch" "old-branch" "$(field 3 "$rowl")"
check "done from a legacy start: head"   "abc1234"    "$(field 4 "$rowl")"
check "done from a legacy start: target" "-"           "$(field 11 "$rowl")"
git switch -q main

# --- items: the run's grouped issues ---------------------------------------
# `findings` is prose for a reader; `items` is the same list as data, so a side panel
# can show each issue and its status without parsing markdown.
good='[{"ns":[1,3],"track":"bug","severity":"high","verdict":"confirmed","file":"a.sprout","line":10,"summary":"one","decision":null},
{"ns":[2],"track":"bug","severity":"low","verdict":"refuted","file":"b.sprout","line":5,"summary":"two","decision":null},
{"ns":[4],"track":"cleanup","severity":"low","verdict":"confirmed","file":"c.sprout","line":7,"summary":"three","decision":"do"}]'
ipath=$(bash "$LEDGER" items itm)
check "items path names the run"        "1"  "$(printf '%s' "$ipath" | grep -c "items-itm.json$")"
check "items path is under GIT_DIR"     "1"  "$(printf '%s' "$ipath" | grep -c '/claude-review/')"
check "items without --write writes nothing" "0" "$([ -e "$ipath" ] && echo 1 || echo 0)"
wpath=$(printf '%s' "$good" | bash "$LEDGER" items itm --write)
check "items --write prints the path"   "$ipath" "$wpath"
check "items --write stores the input"  "0" "$(printf '%s' "$good" | cmp -s - "$ipath"; echo $?)"
check "items --write leaves no temp file" "0" "$(ls -A "${ipath%/*}" | grep -c '^\.tmp\.')"

# A rejected write exits 2 and keeps the earlier file.
rejects() {
  local what="$1" body="$2"
  printf '%s' "$body" | bash "$LEDGER" items itm --write > /dev/null 2> "$R/err.txt"
  check "items rejects $what"           "2"  "$?"
  check "items keeps the file after $what" "0" "$(printf '%s' "$good" | cmp -s - "$ipath"; echo $?)"
}
rejects "bad JSON"                      'not json'
rejects "a non-array"                   '{"ns":[1]}'
rejects "a missing field" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"decision":null}]'
rejects "a bad severity" \
  '[{"ns":[1],"track":"bug","severity":"critical","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":null}]'
rejects "a bad track" \
  '[{"ns":[1],"track":"nit","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":null}]'
rejects "a bad verdict" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"maybe","file":"a","line":1,"summary":"s","decision":null}]'
rejects "a bad decision" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":"later"}]'
rejects "a bug with a decision" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":"do"}]'
rejects "empty ns" \
  '[{"ns":[],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":null}]'
rejects "non-integer ns" \
  '[{"ns":["1"],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":null}]'
rejects "a string line" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":"1","summary":"s","decision":null}]'
rejects "a multi-line summary" \
  '[{"ns":[1],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s\nt","decision":null}]'
rejects "a duplicate n across issues" \
  '[{"ns":[1,2],"track":"bug","severity":"low","verdict":"confirmed","file":"a","line":1,"summary":"s","decision":null},
{"ns":[2],"track":"bug","severity":"low","verdict":"confirmed","file":"b","line":2,"summary":"t","decision":null}]'
printf '[]' | bash "$LEDGER" items empty-items --write > /dev/null
check "items accepts an empty list"     "0"  "$?"
printf 'x' | bash "$LEDGER" items "../../escape" --write > /dev/null 2>&1
check "items rejects a traversing id"   "2"  "$?"

# --- status ---------------------------------------------------------------
spath="${ipath%/*}/status-itm.tsv"
st() { bash "$LEDGER" status "$@" > /dev/null 2> "$R/err.txt"; }
st itm 1 bogus
check "status rejects a bad word"       "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 1x open
check "status rejects a non-digit n"    "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 9 open
check "status rejects an n not in items" "1" "$([ $? -ne 0 ] && echo 1 || echo 0)"
st no-items 1 open
check "status needs an items file"      "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 1 fixed
check "fixed needs a sha"               "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 1 fixed xyz1234
check "status rejects a non-hex sha"    "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 1 fixed abc123
check "status rejects a 6-char sha"     "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
st itm 1 fixed "$(printf 'a%.0s' $(seq 41))"
check "status rejects a 41-char sha"    "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"
check "rejected statuses write nothing" "0"  "$([ -e "$spath" ] && echo 1 || echo 0)"
st "../../escape" 1 open
check "status rejects a traversing id"  "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"

# --- issues ---------------------------------------------------------------
want="1,3	bug	high	open	-	a.sprout:10	one
2	bug	low	refuted	-	b.sprout:5	two
4	cleanup	low	open	-	c.sprout:7	three"
check "issues with no status: open, or refuted" "$want" "$(bash "$LEDGER" issues itm)"
bash "$LEDGER" issues no-items > /dev/null 2>&1
check "issues needs an items file"      "1"  "$([ $? -ne 0 ] && echo 1 || echo 0)"

# The second n of a grouped issue sets the whole issue's status.
st itm 3 fixed abc1234
check "status is recorded"              "1"  "$(wc -l < "$spath" | tr -d ' ')"
check "a status row has four fields"    "4"  "$(awk -F'\t' '{ print NF }' "$spath")"
check "status row: n, status, sha"      "3	fixed	abc1234" \
  "$(awk -F'\t' '{ print $2 "\t" $3 "\t" $4 }' "$spath")"
want="1,3	bug	high	fixed	abc1234	a.sprout:10	one
2	bug	low	refuted	-	b.sprout:5	two
4	cleanup	low	open	-	c.sprout:7	three"
check "a grouped issue takes its second n's status" "$want" "$(bash "$LEDGER" issues itm)"

# The latest line wins, whichever of the issue's numbers it names.
st itm 1 open
check "the latest status wins"          "open	-" \
  "$(bash "$LEDGER" issues itm | sed -n 1p | awk -F'\t' '{ print $4 "\t" $5 }')"
st itm 3 skipped
check "the latest status wins again"    "skipped	-" \
  "$(bash "$LEDGER" issues itm | sed -n 1p | awk -F'\t' '{ print $4 "\t" $5 }')"

# A refuted issue shows `refuted` until a status line says otherwise.
st itm 2 wontfix
check "a status overrides refuted"      "wontfix" \
  "$(bash "$LEDGER" issues itm | sed -n 2p | awk -F'\t' '{ print $4 }')"
st itm 4 fixed 0123456789abcdef0123456789abcdef01234567
check "a 40-char sha is accepted"       "fixed	0123456789abcdef0123456789abcdef01234567" \
  "$(bash "$LEDGER" issues itm | sed -n 3p | awk -F'\t' '{ print $4 "\t" $5 }')"
check "status and issues leave count alone" "3" "$(bash "$LEDGER" count)"

# --- one ledger for every worktree ------------------------------------------
# `open --pr` and `open --branch` file a run under another branch's name, so a review
# done in worktree A of a branch checked out in worktree B must be visible from B.
git worktree add -q -b wt-a "$R/wtA"
git worktree add -q -b wt-b "$R/wtB"
cd "$R/wtA" || exit 1
idw=$(bash "$LEDGER" open --branch wt-b)
bash "$LEDGER" done "$idw" 2 1
check "the reviewing worktree is not credited" "0" "$(bash "$LEDGER" count)"
check "the run is in the common ledger" "2" "$(grep -c "$idw" "$runs")"
check "no per-worktree ledger is written" "0" \
  "$([ -e "$(git rev-parse --absolute-git-dir)/claude-review/runs.tsv" ] && echo 1 || echo 0)"
cd "$R/wtB" || exit 1
check "another worktree sees the run"   "1"  "$(bash "$LEDGER" count)"
check "show sees it too"                "review:1 2 found 1 real" "$(bash "$LEDGER" show)"
cd "$R" || exit 1
check "the main checkout sees it"       "1"  "$(bash "$LEDGER" count --branch wt-b)"

# Rows written before the move sit in the worktree's own runs.tsv and stay readable.
cd "$R/wtB" || exit 1
legacy="$(git rev-parse --absolute-git-dir)/claude-review"
mkdir -p "$legacy"
printf '2020-01-01T00:00:00Z\tdone\twt-b\tdeadbee\tW-1\t3\t1\thigh\n' >> "$legacy/runs.tsv"
check "a legacy per-worktree row counts" "2" "$(bash "$LEDGER" count)"
check "a legacy row feeds show"         "review:2 5 found 2 real" "$(bash "$LEDGER" show)"
# A row present in both files is one row.
awk -F'\t' '$5=="W-1"' "$legacy/runs.tsv" >> "$runs"
check "an identical row in both counts once" "2" "$(bash "$LEDGER" count)"
check "and sums once"                   "review:2 5 found 2 real" "$(bash "$LEDGER" show)"
printf '2020-01-01T00:00:00Z\tstart\twt-b\tabc1234\tW-2\t-\t-\t-\t-\t-\n' >> "$legacy/runs.tsv"
bash "$LEDGER" done W-2 1 1
check "done finds a legacy start row"   "3"  "$(bash "$LEDGER" count)"
check "and writes to the common ledger" "1"  "$(awk -F'\t' '$2=="done" && $5=="W-2"' "$runs" | wc -l | tr -d ' ')"
check "and not to the legacy one"       "0"  "$(awk -F'\t' '$2=="done" && $5=="W-2"' "$legacy/runs.tsv" | wc -l | tr -d ' ')"

# Sibling files: the common dir first, then the legacy one.
printf '%s' "$good" > "$legacy/items-leg.json"
printf '2020-01-01T00:00:00Z\t1\tfixed\tabc1234\n' > "$legacy/status-leg.tsv"
check "a legacy findings path is found" "$legacy/items-leg.json" "$(bash "$LEDGER" items leg)"
check "issues reads legacy items and status" "fixed	abc1234" \
  "$(bash "$LEDGER" issues leg | sed -n 1p | awk -F'\t' '{ print $4 "\t" $5 }')"
bash "$LEDGER" status leg 4 skipped
check "a new status goes to the common dir" "1" \
  "$([ -f "$(dirname "$runs")/status-leg.tsv" ] && echo 1 || echo 0)"
check "issues merges legacy and common status" "fixed	abc1234|skipped	-" \
  "$(bash "$LEDGER" issues leg | awk -F'\t' 'NR==1 || NR==3 { printf "%s%s\t%s", s, $4, $5; s="|" }')"
printf '%s' "$good" | bash "$LEDGER" items leg --write > /dev/null
check "--write targets the common dir"  "$(dirname "$runs")/items-leg.json" "$(bash "$LEDGER" items leg)"
cd "$R" || exit 1

# In the main checkout the git dir is the common dir, so there is no second file.
check "the main checkout counts as before" "3" "$(bash "$LEDGER" count)"

# --- runs and dir: what the pr-tracker plugin reads ---------------------------
# `runs` is every completed run once, with the dedupe rule count and show use (the FIRST
# `done` row of an id), padded to eleven columns and ordered by `when`. `dir` names the
# directories the ledger reads, the common one first.
mkdir "$R/rr"
cd "$R/rr" || exit 1
git init -q -b main .
git config user.email t@t
git config user.name t
echo a > a.txt
git add a.txt
git commit -qm first
rrdir="$(git rev-parse --path-format=absolute --git-common-dir)/claude-review"
mkdir -p "$rrdir"
{
  printf '2026-10-30T10:30:00Z\tdone\tb-late\thhh\tr2\t4\t4\thigh\t6\t5\tpr:782\n'
  printf '2026-10-30T08:00:00Z\tstart\tb-early\taaa\tr1\t-\t-\t-\t-\t-\t-\n'
  printf '2026-10-30T09:00:00Z\tdone\tb-early\taaa\tr1\t2\t1\n'
  printf '2026-10-30T09:30:00Z\tdone\tb-early\taaa\tr1\t9\t9\tlow\n'
  printf '2026-10-30T09:15:00Z\tdone\told\tdead\tr0\t3\t1\thigh\t2\n'
  printf '2026-10-30T11:00:00Z\tstart\tb-open\tbbb\tr3\t-\t-\t-\t-\t-\t-\n'
} > "$rrdir/runs.tsv"
tab=$'\t'
check "runs: one line per completed run, oldest first" "r1 r0 r2" \
  "$(bash "$LEDGER" runs | awk -F'\t' '{ printf "%s%s", s, $5; s=" " }')"
check "runs: a started run is left out" "0" "$(bash "$LEDGER" runs | grep -c "${tab}start${tab}")"
check "runs: the first done row of an id wins" "2${tab}1" \
  "$(bash "$LEDGER" runs | awk -F'\t' '$5=="r1" { print $6 "\t" $7 }')"
check "runs: every line has 11 columns" "11" \
  "$(bash "$LEDGER" runs | awk -F'\t' '{ print NF }' | sort -u)"
check "runs: a 7-field row is padded with '-'" "-${tab}-${tab}-${tab}-" \
  "$(bash "$LEDGER" runs | awk -F'\t' '$5=="r1" { print $8 "\t" $9 "\t" $10 "\t" $11 }')"
check "runs: an 8-field row is padded too" "high${tab}2${tab}-${tab}-" \
  "$(bash "$LEDGER" runs | awk -F'\t' '$5=="r0" { print $8 "\t" $9 "\t" $10 "\t" $11 }')"
check "runs: an 11-field row is kept as is" "high${tab}6${tab}5${tab}pr:782" \
  "$(bash "$LEDGER" runs | awk -F'\t' '$5=="r2" { print $8 "\t" $9 "\t" $10 "\t" $11 }')"
check "runs: as many lines as count and show see" "3" "$(bash "$LEDGER" runs | wc -l | tr -d ' ')"
check "dir: the main checkout has one dir" "$rrdir" "$(bash "$LEDGER" dir)"

# A worktree reads its own legacy file too: legacy rows come in, an identical row in
# both files is one line, and `dir` lists the common dir first.
git worktree add -q -b rr-wt "$R/rrwt"
cd "$R/rrwt" || exit 1
rrlegacy="$(git rev-parse --absolute-git-dir)/claude-review"
mkdir -p "$rrlegacy"
printf '2026-10-30T08:30:00Z\tdone\told\tdead\tL9\t1\t1\n' > "$rrlegacy/runs.tsv"
printf '2026-10-30T10:30:00Z\tdone\tb-late\thhh\tr2\t4\t4\thigh\t6\t5\tpr:782\n' >> "$rrlegacy/runs.tsv"
check "runs: a legacy row is included, once" "L9 r1 r0 r2" \
  "$(bash "$LEDGER" runs | awk -F'\t' '{ printf "%s%s", s, $5; s=" " }')"
check "dir: common first, then the legacy dir" "$rrdir|$rrlegacy" \
  "$(bash "$LEDGER" dir | paste -sd'|' -)"
cd "$R" || exit 1

# Outside a repository the ledger must stay quiet rather than erroring: a caller
# may run it from any directory.
cd /tmp || exit 1
check "outside a repo, count is zero"  "0"  "$(bash "$LEDGER" count)"
check "outside a repo, show is silent" ""   "$(bash "$LEDGER" show 2>/dev/null)"
bash "$LEDGER" runs > "$R/out.txt" 2> "$R/err.txt"
check "outside a repo, runs exits 0"   "0"  "$?"
check "outside a repo, runs prints nothing" "00" "$(wc -c < "$R/out.txt" | tr -d ' ')$(wc -c < "$R/err.txt" | tr -d ' ')"
bash "$LEDGER" dir > "$R/out.txt" 2> "$R/err.txt"
check "outside a repo, dir exits 0"    "0"  "$?"
check "outside a repo, dir prints nothing" "00" "$(wc -c < "$R/out.txt" | tr -d ' ')$(wc -c < "$R/err.txt" | tr -d ' ')"

if [ "$fail" -ne 0 ]; then
  echo "==> review-ledger tests FAILED" >&2
  exit 1
fi
echo "==> review-ledger tests passed"
