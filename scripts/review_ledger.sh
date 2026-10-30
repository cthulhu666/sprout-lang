#!/usr/bin/env bash
# Ledger for `/sprout-review`: how many reviews have run on this branch.
#
# Append-only TSV rather than JSON, for two reasons. Two sessions can write at once,
# and an append needs no read-modify-write; and the status line reads this on a 300ms
# debounce, so it must not parse anything.
#
# Lives under the common git dir, invisible to `git status`, so a review can never
# dirty a commit. One ledger serves every worktree of a repo: a run filed under
# another branch's name is visible from the worktree that has that branch checked
# out. Rows carry their branch. Files an older version wrote under the worktree's
# own git dir are still read, never written.
#
#   review_ledger.sh open [--pr <n> | --branch <name>]
#     record a run starting; prints the run id. The row names the REVIEWED code: the PR's
#     head branch (via `gh pr view`; a fork's is `<owner>:<branch>`), or <name> (a local
#     branch, else origin's; recorded bare), or with no flag the checked-out branch.
#     A failing `gh` is an error, never a fallback to the checked-out branch.
#   review_ledger.sh done <id> <found> <confirmed> [effort] [cleanups] [cleanups-confirmed]
#     branch, head and target are copied from the id's start row; no start row is an error
#   review_ledger.sh findings <id> [--write]   path of that run's findings file
#   review_ledger.sh raw <id> [--write]        path for the findings as each pass worded them
#   review_ledger.sh items <id> [--write]      path of that run's grouped issues (JSON)
#     --write: read stdin into that file (atomically), then print the path; `items` also
#     validates it: an array of {ns:[n,..] (positive ints), track:bug|cleanup, severity:low|medium|high,
#     verdict:confirmed|unverified|refuted, file, line, summary (one line), decision:do|skip|null}
#     with no n repeated across issues
#   review_ledger.sh status <id> <n> <open|fixed|skipped|wontfix> [<sha>]
#     append a status for issue <n> (one of the items' ns); `fixed` needs a 7-40 hex sha
#   review_ledger.sh issues <id>
#     one line per issue, tab-separated, latest status joined in (a status on any of an
#     issue's ns sets it; no line: `refuted` for a refuted issue, else `open`):
#       ns (comma-joined) | track | severity | status | sha or - | file:line | summary
#   review_ledger.sh count [--branch <name>]   completed runs on this (or that) branch
#   review_ledger.sh show            one-line summary, for the status line
set -uo pipefail

# Sets LEDGER_DIR (written and read) and LEGACY_DIR (read only; empty in the main
# checkout, where the git dir is the common dir, so nothing is read twice).
resolve_dirs() {
  local out common git_dir
  out=$(git rev-parse --path-format=absolute --git-common-dir --absolute-git-dir 2>/dev/null) \
    || return 1
  common="${out%%$'\n'*}"
  git_dir="${out#*$'\n'}"
  LEDGER_DIR="$common/claude-review"
  LEGACY_DIR=""
  [ "$git_dir" -ef "$common" ] || LEGACY_DIR="$git_dir/claude-review"
}

# Every row, legacy file first, identical rows once.
read_rows() {
  resolve_dirs || return 1
  {
    [ -n "$LEGACY_DIR" ] && [ -f "$LEGACY_DIR/runs.tsv" ] && cat "$LEGACY_DIR/runs.tsv"
    [ -f "$LEDGER_DIR/runs.tsv" ] && cat "$LEDGER_DIR/runs.tsv"
  } | awk '!seen[$0]++'
}

branch_name() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'DETACHED'
}

# Columns: when, state, branch, head, run-id, found, confirmed, effort, cleanups,
# cleanups-confirmed, target. A `start` row writes "-" for the five it cannot know yet.
# `target` is `pr:<n>`, `branch:<name>` or "-".
#
# The last four were appended rather than inserted, so rows have seven to eleven
# fields. Every reader selects by number and tests NF past $7, so all widths
# parse — which is the only reason these columns could be added.
append_row() {
  local file="$1"
  shift
  mkdir -p "$(dirname "$file")" || return 1
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$file"
}

# A branch name or other free text bound for a column: no tab, newline or CR, and
# no leading dash, so no argument can split a row or pose as an option.
safe_token() {
  case "${1:-}" in
    "" | -* | *$'\t'* | *$'\n'* | *$'\r'*) return 1 ;;
  esac
}

# 7-40 hex characters: a commit id or an abbreviation of one.
is_hex_sha() {
  case "${1:-}" in
    "" | *[!0-9a-fA-F]*) return 1 ;;
  esac
  [ "${#1}" -ge 7 ] && [ "${#1}" -le 40 ]
}

# A count, or "-": digits only, so no argument can carry a tab into the row.
count_or_dash() {
  case "${1:-}" in
    "" | *[!0-9]*) printf -- '-' ;;
    *) printf '%s' "$1" ;;
  esac
}

# The PR's head branch, head commit, cross-repo flag and head repository owner, one per
# line. gh failing is the caller's error: falling back to the checked-out branch would
# file the run under the wrong code.
resolve_pr() {
  gh pr view "$1" --json headRefName,headRefOid,isCrossRepository,headRepositoryOwner \
    --jq '.headRefName, .headRefOid, .isCrossRepository, (.headRepositoryOwner.login // "")' \
    || { echo "review_ledger.sh open: 'gh pr view ${1}' failed; no row written" >&2; return 1; }
}

cmd_open() {
  local file branch head id target="-" n resolved oid cross owner
  resolve_dirs || { echo "not a git repository" >&2; return 1; }
  file="$LEDGER_DIR/runs.tsv"
  [ $# -le 2 ] || { echo "review_ledger.sh open: too many arguments" >&2; return 2; }
  case "${1:-}" in
    "")
      branch=$(branch_name)
      head=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown') ;;
    --pr)
      n="${2:-}"
      case "$n" in
        "" | *[!0-9]*) echo "review_ledger.sh open: --pr needs a PR number (got '${n}')" >&2
                       return 2 ;;
      esac
      resolved=$(resolve_pr "$n") || return 1
      { IFS= read -r branch; IFS= read -r oid; IFS= read -r cross; IFS= read -r owner; } \
        <<< "$resolved"
      safe_token "$branch" || { echo "review_ledger.sh open: bad branch from gh" >&2; return 1; }
      is_hex_sha "$oid" || { echo "review_ledger.sh open: bad head from gh" >&2; return 1; }
      # A fork's branch is filed as <owner>:<branch>, so it cannot collide with a local one.
      case "$cross" in
        false) ;;
        true) safe_token "$owner" \
                || { echo "review_ledger.sh open: bad fork owner from gh" >&2; return 1; }
              branch="${owner}:${branch}" ;;
        *) echo "review_ledger.sh open: bad isCrossRepository from gh" >&2; return 1 ;;
      esac
      if git cat-file -e "${oid}^{commit}" 2>/dev/null; then
        head=$(git rev-parse --short "$oid" 2>/dev/null) || head="${oid:0:8}"
      else
        head="${oid:0:8}"
      fi
      target="pr:${n}" ;;
    --branch)
      branch="${2:-}"
      safe_token "$branch" \
        || { echo "review_ledger.sh open: --branch needs a branch name (got '${branch}')" >&2
             return 2; }
      # The local branch, else origin's; the bare name is what is recorded either way.
      head=$(git rev-parse --verify --short "refs/heads/${branch}^{commit}" 2>/dev/null) \
        || head=$(git rev-parse --verify --short "refs/remotes/origin/${branch}^{commit}" 2>/dev/null) \
        || { echo "review_ledger.sh open: no such branch '${branch}'" >&2; return 1; }
      target="branch:${branch}" ;;
    *) echo "review_ledger.sh open: unknown argument '${1}' (--pr <n> | --branch <name>)" >&2
       return 2 ;;
  esac
  # Seconds since epoch plus the pid: unique without coordinating with readers.
  id="$(date +%s)-$$"
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" start "$branch" "$head" "$id" \
             - - - - - "$target"
  printf '%s\n' "$id"
}

cmd_done() {
  local file branch head target start id found confirmed effort cleanups cleanups_confirmed
  id="${1:?usage: review_ledger.sh done <id> <found> <confirmed> [effort] [cleanups] [cleanups-confirmed]}"
  found="${2:--}"
  confirmed="${3:--}"
  # The level arrives from an argument the skill parsed, so only the known
  # vocabulary is stored: a tab in it would split the row, and every reader here
  # and in the status line addresses columns by number.
  case "${4:-}" in
    low | medium | high | xhigh | max) effort="$4" ;;
    *) effort=- ;;
  esac
  # Cleanups are not bugs, so they never share `found` or `confirmed`.
  cleanups=$(count_or_dash "${5:-}")
  cleanups_confirmed=$(count_or_dash "${6:-}")
  resolve_dirs || { echo "not a git repository" >&2; return 1; }
  file="$LEDGER_DIR/runs.tsv"
  # Branch, head and target come from the start row, not from HEAD: HEAD may have
  # moved, or the run may have reviewed a branch that was never checked out. A
  # legacy start row has no target column, so it reads as "-".
  start=$(read_rows | awk -F'\t' -v i="$id" '
    $2=="start" && $5==i { print $3 "\t" $4 "\t" (NF >= 11 ? $11 : "-"); exit }')
  if [ -z "$start" ]; then
    echo "review_ledger.sh done: no start row for run '${id}'" >&2
    return 1
  fi
  IFS=$'\t' read -r branch head target <<< "$start"
  append_row "$file" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" done "$branch" "$head" "$id" \
             "$found" "$confirmed" "$effort" "$cleanups" "$cleanups_confirmed" "$target"
}

# Where a run's findings are written, as a sibling of the TSV rather than a column
# in it: findings are multi-line prose, and the status line must keep parsing this
# ledger with one `awk`. The counts say a review happened; this says what it found,
# so a reader can be pointed at the list instead of at a number.
#
# The id arrives from a workflow result, so it is not trusted to be a bare token —
# a `/` or `..` in it would resolve outside the ledger directory.
#
# 5th argument: none prints the path to write, in the common dir. `read` prints the file
# that exists, the common dir's first, else a legacy per-worktree one, else the path to
# write. `legacy` prints the legacy file if it exists, else an empty line.
# Paths are absolute (`resolve_dirs`), so a reader whose cwd is unknown can use them.
ledger_sibling() {
  local id kind ext what mode name
  what="${1:?}" ; kind="${2:?}" ; ext="${3:?}" ; id="${4:-}" ; mode="${5:-}"
  case "$id" in
    "" | *[/\\]* | *..*)
      echo "review_ledger.sh ${what}: a run id must be a bare token (got '${id}')" >&2
      return 2 ;;
  esac
  resolve_dirs || { echo "not a git repository" >&2; return 1; }
  mkdir -p "$LEDGER_DIR" || return 1
  name="$kind-$id.$ext"
  case "$mode" in
    read)
      if [ ! -f "$LEDGER_DIR/$name" ] && [ -n "$LEGACY_DIR" ] && [ -f "$LEGACY_DIR/$name" ]; then
        printf '%s/%s\n' "$LEGACY_DIR" "$name"
      else
        printf '%s/%s\n' "$LEDGER_DIR" "$name"
      fi ;;
    legacy)
      if [ -n "$LEGACY_DIR" ] && [ -f "$LEGACY_DIR/$name" ]; then
        printf '%s/%s\n' "$LEGACY_DIR" "$name"
      else
        printf '\n'
      fi ;;
    *) printf '%s/%s\n' "$LEDGER_DIR" "$name" ;;
  esac
}

# `--write` reads stdin into the sibling path. The ledger dir sits inside the main
# checkout's `.git`, which an isolated session's file tools refuse in a worktree.
# Temp file then `mv`, so an interrupted write leaves no half file; empty stdin is
# an error so a failed upstream command cannot blank an earlier file.
ledger_sibling_cmd() {
  local what="$1" kind="$2" ext="$3" id="${4:-}" flag="${5:-}" path tmp
  case "$flag" in
    "" | --write) ;;
    *) echo "review_ledger.sh ${what}: unknown argument '${flag}' (only --write)" >&2
       return 2 ;;
  esac
  if [ "$flag" = --write ]; then
    path=$(ledger_sibling "$what" "$kind" "$ext" "$id") || return $?
    tmp=$(mktemp "${path%/*}/.tmp.XXXXXX") || return 1
    cat > "$tmp" || { rm -f "$tmp"; return 1; }
    if [ ! -s "$tmp" ]; then
      rm -f "$tmp"
      echo "review_ledger.sh ${what}: empty stdin, nothing written" >&2
      return 2
    fi
    # An optional check of the content, named by the caller; a bad file is never moved in.
    if [ -n "${SIBLING_VALIDATE:-}" ] && ! "$SIBLING_VALIDATE" "$tmp" "$what"; then
      rm -f "$tmp"
      return 2
    fi
    mv -f "$tmp" "$path" || { rm -f "$tmp"; return 1; }
  else
    path=$(ledger_sibling "$what" "$kind" "$ext" "$id" read) || return $?
  fi
  printf '%s\n' "$path"
}

cmd_findings() { ledger_sibling_cmd findings findings md "$@"; }

# The findings as each pass worded them. The report next to it is judged and grouped
# by a reader, so it cannot answer what the passes actually said: `OVERLAP_MIN` sat
# at 0.5 for three runs on adjectives ("synonyms, ~0.3 overlap") because the
# summaries it scored were never written down. Writing them down is what let the
# threshold be measured, and measuring it is what removed it. JSON, not prose — this
# one is for replaying a decision over the raw claims, not for reading.
cmd_raw() { ledger_sibling_cmd raw raw json "$@"; }

# The same findings as the markdown file, grouped into issues, as data. A panel reads
# this and the status file instead of parsing prose. `ns` are the run's `n` numbers
# that the reader grouped into one issue; no n may belong to two issues.
validate_items() {
  python3 -I - "$1" "$2" <<'PY'
import json, sys
path, what = sys.argv[1], sys.argv[2]
def bad(msg):
    print("review_ledger.sh %s: %s" % (what, msg), file=sys.stderr)
    sys.exit(1)
def isint(v):
    return isinstance(v, int) and not isinstance(v, bool)
def oneline(v):
    return isinstance(v, str) and not any(c in v for c in "\t\n\r")
try:
    with open(path) as f:
        items = json.load(f)
except ValueError as e:
    bad("not valid JSON (%s)" % e)
if not isinstance(items, list):
    bad("expected an array of issues")
enums = {"track": ("bug", "cleanup"), "severity": ("low", "medium", "high"),
         "verdict": ("confirmed", "unverified", "refuted")}
seen = set()
for i, it in enumerate(items):
    where = "issue %d" % i
    if not isinstance(it, dict):
        bad("%s is not an object" % where)
    for k in ("ns", "track", "severity", "verdict", "file", "line", "summary", "decision"):
        if k not in it:
            bad("%s: missing '%s'" % (where, k))
    ns = it["ns"]
    if not isinstance(ns, list) or not ns or not all(isint(n) and n >= 1 for n in ns):
        bad("%s: ns must be a non-empty array of positive integers" % where)
    for n in ns:
        if n in seen:
            bad("%s: n %d appears in more than one issue" % (where, n))
        seen.add(n)
    for k, ok in enums.items():
        if not isinstance(it[k], str) or it[k] not in ok:
            bad("%s: %s must be one of %s" % (where, k, "|".join(ok)))
    if it["decision"] not in ("do", "skip", None):
        bad("%s: decision must be do, skip or null" % where)
    if not oneline(it["file"]) or not it["file"]:
        bad("%s: file must be a one-line string" % where)
    if not isint(it["line"]):
        bad("%s: line must be an integer" % where)
    if it["track"] == "bug" and it["decision"] is not None:
        bad("%s: a bug has no decision (null)" % where)
    if not oneline(it["summary"]):
        bad("%s: summary must be a one-line string" % where)
PY
}

cmd_items() { SIBLING_VALIDATE=validate_items ledger_sibling_cmd items items json "$@"; }

# A status is a line appended to a sibling TSV, never an edit of the items file, so
# the issues stay as the review wrote them and two sessions cannot clobber each other.
# Columns: when, n, status, sha-or-dash. The latest line for an issue's n wins.
cmd_status() {
  local id="${1:-}" n="${2:-}" st="${3:-}" sha="${4:-}" ipath spath
  if [ $# -lt 3 ] || [ $# -gt 4 ]; then
    echo "usage: review_ledger.sh status <id> <n> <open|fixed|skipped|wontfix> [<sha>]" >&2
    return 2
  fi
  ipath=$(ledger_sibling status items json "$id" read) || return $?
  spath=$(ledger_sibling status status tsv "$id") || return $?
  [ -f "$ipath" ] || { echo "review_ledger.sh status: no items for run '${id}'" >&2; return 1; }
  case "$n" in
    "" | *[!0-9]*) echo "review_ledger.sh status: n must be digits (got '${n}')" >&2
                   return 1 ;;
  esac
  [ "${#n}" -le 9 ] || { echo "review_ledger.sh status: n too long" >&2; return 1; }
  n=$((10#$n))
  case "$st" in
    open | fixed | skipped | wontfix) ;;
    *) echo "review_ledger.sh status: status must be open|fixed|skipped|wontfix (got '${st}')" >&2
       return 1 ;;
  esac
  if [ -n "$sha" ] && ! is_hex_sha "$sha"; then
    echo "review_ledger.sh status: sha must be 7-40 hex characters (got '${sha}')" >&2
    return 1
  fi
  if [ "$st" = fixed ] && [ -z "$sha" ]; then
    echo "review_ledger.sh status: 'fixed' needs the commit sha" >&2
    return 1
  fi
  python3 -I - "$ipath" "$n" <<'PY' || { echo "review_ledger.sh status: n ${n} is not in run '${id}'" >&2; return 1; }
import json, sys
with open(sys.argv[1]) as f:
    items = json.load(f)
sys.exit(0 if int(sys.argv[2]) in {n for it in items for n in it["ns"]} else 1)
PY
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$n" "$st" "${sha:--}" >> "$spath"
}

# One line per issue, items joined with their status; the columns are in the header.
# File order, not timestamp, decides "latest": two lines can share a second.
cmd_issues() {
  local ipath spath lpath
  ipath=$(ledger_sibling issues items json "${1:-}" read) || return $?
  spath=$(ledger_sibling issues status tsv "${1:-}") || return $?
  lpath=$(ledger_sibling issues status tsv "${1:-}" legacy) || return $?
  [ -f "$ipath" ] || { echo "review_ledger.sh issues: no items for run '${1}'" >&2; return 1; }
  # Legacy status lines first, so the common dir's later lines win.
  python3 -I - "$ipath" "$lpath" "$spath" <<'PY'
import json, os, sys
try:
    with open(sys.argv[1]) as f:
        items = json.load(f)
except ValueError as e:
    print("review_ledger.sh issues: unreadable items file (%s)" % e, file=sys.stderr)
    sys.exit(1)
events = []
for path in sys.argv[2:]:
    if path and os.path.exists(path):
        with open(path) as f:
            for line in f.read().splitlines():
                p = line.split("\t")
                if len(p) >= 3 and p[1].isascii() and p[1].isdigit():
                    events.append((int(p[1]), p[2], p[3] if len(p) > 3 else "-"))
for it in items:
    ns = set(it["ns"])
    st, sha = ("refuted" if it["verdict"] == "refuted" else "open"), "-"
    for n, s, h in events:
        if n in ns:
            st, sha = s, h
    print("\t".join([",".join(str(n) for n in it["ns"]), it["track"], it["severity"], st, sha,
                     "%s:%d" % (it["file"], it["line"]), it["summary"]]))
PY
}

# A run counts as complete only once, however many `done` rows name it: the id
# column is what dedupes, so a retry cannot inflate the number.
cmd_count() {
  local branch
  case "${1:-}" in
    "") branch=$(branch_name) ;;
    --branch)
      branch="${2:-}"
      safe_token "$branch" \
        || { echo "review_ledger.sh count: --branch needs a branch name" >&2; return 2; }
      [ $# -eq 2 ] || { echo "review_ledger.sh count: too many arguments" >&2; return 2; } ;;
    *) echo "review_ledger.sh count: unknown argument '${1}' (only --branch <name>)" >&2
       return 2 ;;
  esac
  read_rows | awk -F'\t' -v b="$branch" '$2=="done" && $3==b { seen[$5]=1 }
                                         END { print length(seen) }'
}

cmd_show() {
  local branch
  branch=$(branch_name)
  # Sum only a run's FIRST `done` row. Counting distinct ids while summing every
  # row would let one re-reported run inflate the totals but not the run count.
  #
  # The level is the latest counted run's, not an aggregate — averaging or
  # listing levels answers nothing, and "how hard was the last look" is the
  # question a bare `review:3` leaves open. Assigned on every counted row, so a
  # later run that recorded no level clears it rather than inheriting the
  # previous one's.
  read_rows | awk -F'\t' -v b="$branch" '
    $3==b && $2=="done"&& !($5 in seen) {
                          seen[$5]=1
                          if ($6 != "-") found += $6
                          if ($7 != "-") confirmed += $7
                          level = (NF >= 8 && $8 != "-") ? $8 : "" }
    END { n = length(seen)
          if (n == 0) exit
          printf "review:%d", n
          if (confirmed != "") printf " %d found %d real", found, confirmed
          if (level != "") printf " @%s", level
          printf "\n" }'
}

case "${1:-show}" in
  open)  shift; cmd_open "$@" ;;
  done)  shift; cmd_done "$@" ;;
  findings) shift; cmd_findings "$@" ;;
  raw) shift; cmd_raw "$@" ;;
  items) shift; cmd_items "$@" ;;
  status) shift; cmd_status "$@" ;;
  issues) shift; cmd_issues "$@" ;;
  count) shift; cmd_count "$@" ;;
  show)  cmd_show ;;
  *) echo "usage: review_ledger.sh {open [--pr <n>|--branch <name>]|done <id> <found> <confirmed> [effort] [cleanups] [cleanups-confirmed]|findings <id> [--write]|raw <id> [--write]|items <id> [--write]|status <id> <n> <open|fixed|skipped|wontfix> [sha]|issues <id>|count [--branch <name>]|show}" >&2
     exit 2 ;;
esac
