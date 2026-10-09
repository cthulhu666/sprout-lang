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
#   review_ledger.sh done <id> <found> <confirmed> [effort] [cleanups]
#   review_ledger.sh findings <id>   print the path to write that run's findings to
#   review_ledger.sh raw <id>        path for that run's findings as each pass worded them
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

# Columns: when, state, branch, head, run-id, found, confirmed, effort, cleanups.
# A `start` row writes "-" for the four it cannot know yet.
#
# `effort` and `cleanups` were appended rather than inserted, so rows have seven,
# eight or nine fields. Every reader selects by number and tests NF past $7, so
# all widths parse — which is the only reason these columns could be added.
append_row() {
  local file="$1"
  shift
  mkdir -p "$(dirname "$file")" || return 1
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$file"
}

cmd_open() {
  local file branch head id
  file=$(ledger_path) || { echo "not a git repository" >&2; return 1; }
  branch=$(branch_name)
  head=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')
  # Seconds since epoch plus the pid: unique without coordinating with readers.
  id="$(date +%s)-$$"
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" start "$branch" "$head" "$id" - - - -
  printf '%s\n' "$id"
}

cmd_done() {
  local file branch head id found confirmed effort cleanups
  id="${1:?usage: review_ledger.sh done <id> <found> <confirmed> [effort] [cleanups]}"
  found="${2:--}"
  confirmed="${3:--}"
  # The level arrives from an argument the skill parsed, so only the known
  # vocabulary is stored: a tab in it would split the row, and every reader here
  # and in the status line addresses columns by number.
  case "${4:-}" in
    low | medium | high | xhigh | max) effort="$4" ;;
    *) effort=- ;;
  esac
  # Cleanups are not bugs, so they never share `found`. Digits only, same reason.
  case "${5:-}" in
    "" | *[!0-9]*) cleanups=- ;;
    *) cleanups="$5" ;;
  esac
  file=$(ledger_path) || { echo "not a git repository" >&2; return 1; }
  branch=$(branch_name)
  head=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" done "$branch" "$head" "$id" \
             "$found" "$confirmed" "$effort" "$cleanups"
}

# Where a run's findings are written, as a sibling of the TSV rather than a column
# in it: findings are multi-line prose, and the status line must keep parsing this
# ledger with one `awk`. The counts say a review happened; this says what it found,
# so a reader can be pointed at the list instead of at a number.
#
# The id arrives from a workflow result, so it is not trusted to be a bare token —
# a `/` or `..` in it would resolve outside the ledger directory.
ledger_sibling() {
  local dir id kind ext what
  what="${1:?}" ; kind="${2:?}" ; ext="${3:?}" ; id="${4:-}"
  case "$id" in
    "" | *[/\\]* | *..*)
      echo "review_ledger.sh ${what}: a run id must be a bare token (got '${id}')" >&2
      return 2 ;;
  esac
  # `--git-dir` answers `.git` at the root and an absolute path from a subdirectory,
  # so it cannot be quoted back to a reader whose cwd is unknown. `--absolute-git-dir`
  # is the same directory, spelled the same way from anywhere (git >= 2.13).
  dir=$(git rev-parse --absolute-git-dir 2>/dev/null) \
    || { echo "not a git repository" >&2; return 1; }
  dir="$dir/claude-review"
  mkdir -p "$dir" || return 1
  printf '%s/%s-%s.%s\n' "$dir" "$kind" "$id" "$ext"
}

cmd_findings() { ledger_sibling findings findings md "${1:-}"; }

# The findings as each pass worded them. The report next to it is judged and grouped
# by a reader, so it cannot answer what the passes actually said: `OVERLAP_MIN` sat
# at 0.5 for three runs on adjectives ("synonyms, ~0.3 overlap") because the
# summaries it scored were never written down. Writing them down is what let the
# threshold be measured, and measuring it is what removed it. JSON, not prose — this
# one is for replaying a decision over the raw claims, not for reading.
cmd_raw() { ledger_sibling raw raw json "${1:-}"; }

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
  #
  # The level is the latest counted run's, not an aggregate — averaging or
  # listing levels answers nothing, and "how hard was the last look" is the
  # question a bare `review:3` leaves open. Assigned on every counted row, so a
  # later run that recorded no level clears it rather than inheriting the
  # previous one's.
  awk -F'\t' -v b="$branch" '
    $3==b && $2=="done" && !($5 in seen) {
                          seen[$5]=1
                          if ($6 != "-") found += $6
                          if ($7 != "-") confirmed += $7
                          level = (NF >= 8 && $8 != "-") ? $8 : "" }
    END { n = length(seen)
          if (n == 0) exit
          printf "review:%d", n
          if (confirmed != "") printf " %d found %d real", found, confirmed
          if (level != "") printf " @%s", level
          printf "\n" }' "$file"
}

case "${1:-show}" in
  open)  cmd_open ;;
  done)  shift; cmd_done "$@" ;;
  findings) shift; cmd_findings "$@" ;;
  raw) shift; cmd_raw "$@" ;;
  count) cmd_count ;;
  show)  cmd_show ;;
  *) echo "usage: review_ledger.sh {open|done <id> <found> <confirmed> [effort] [cleanups]|findings <id>|raw <id>|count|show}" >&2
     exit 2 ;;
esac
