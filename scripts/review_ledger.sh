#!/usr/bin/env bash
# Ledger for `/sprout-review`: how many reviews have run on this branch.
#
# Append-only TSV rather than JSON, for two reasons. Two sessions can review the
# same worktree at once, and an append needs no read-modify-write; and the status
# line reads this on a 300ms debounce, so it must not parse anything.
#
# Lives under $GIT_DIR, which is per-worktree and invisible to `git status`, so a
# review can never dirty a commit. Same place `review_gate.py` keeps its state.
#
#   review_ledger.sh open            record a run starting; prints the run id
#   review_ledger.sh done <id> <found> <confirmed>
#   review_ledger.sh count           completed runs on this branch
#   review_ledger.sh show            one-line summary, for the status line
set -uo pipefail

ledger_path() {
  local git_dir
  git_dir=$(git rev-parse --git-dir 2>/dev/null) || return 1
  printf '%s/claude-review/runs.tsv' "$git_dir"
}

branch_name() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'DETACHED'
}

# Columns: when, state, branch, head, run-id, found, confirmed. A `start` row
# writes "-" for the two counts it cannot know yet.
append_row() {
  local file="$1"
  shift
  mkdir -p "$(dirname "$file")" || return 1
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$file"
}

cmd_open() {
  local file branch head id
  file=$(ledger_path) || { echo "not a git repository" >&2; return 1; }
  branch=$(branch_name)
  head=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')
  # Seconds since epoch plus the pid: unique without coordinating with readers.
  id="$(date +%s)-$$"
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" start "$branch" "$head" "$id" - -
  printf '%s\n' "$id"
}

cmd_done() {
  local file branch head id found confirmed
  id="${1:?usage: review_ledger.sh done <id> <found> <confirmed>}"
  found="${2:--}"
  confirmed="${3:--}"
  file=$(ledger_path) || { echo "not a git repository" >&2; return 1; }
  branch=$(branch_name)
  head=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" done "$branch" "$head" "$id" \
             "$found" "$confirmed"
}

# A run counts as complete only once, however many `done` rows name it: the id
# column is what dedupes, so a retry cannot inflate the number.
cmd_count() {
  local file branch
  file=$(ledger_path) || { printf '0\n'; return 0; }
  branch=$(branch_name)
  [ -f "$file" ] || { printf '0\n'; return 0; }
  awk -F'\t' -v b="$branch" '$2=="done" && $3==b { seen[$5]=1 }
                             END { print length(seen) }' "$file"
}

cmd_show() {
  local file branch
  file=$(ledger_path) || return 0
  branch=$(branch_name)
  [ -f "$file" ] || return 0
  # Sum only a run's FIRST `done` row. Counting distinct ids while summing every
  # row would let one re-reported run inflate the totals but not the run count.
  awk -F'\t' -v b="$branch" '
    $3==b && $2=="done" && !($5 in seen) {
                          seen[$5]=1
                          if ($6 != "-") found += $6
                          if ($7 != "-") confirmed += $7 }
    END { n = length(seen)
          if (n == 0) exit
          printf "review:%d", n
          if (confirmed != "") printf " %d found %d real", found, confirmed
          printf "\n" }' "$file"
}

case "${1:-show}" in
  open)  cmd_open ;;
  done)  shift; cmd_done "$@" ;;
  count) cmd_count ;;
  show)  cmd_show ;;
  *) echo "usage: review_ledger.sh {open|done <id> <found> <confirmed>|count|show}" >&2
     exit 2 ;;
esac
