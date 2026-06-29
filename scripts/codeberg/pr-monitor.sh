#!/usr/bin/env bash
# Persistent CI/state monitor for one or more open PRs. Polls each PR
# every 60s and emits one line per state-change per PR:
#
#   PR#<N>: state=<X> merged=<Y> ci=<Z> sha=<S>
#
# Exits with code 0 once every PR has reached a terminal state
# (merged=true, state=closed, or ci=failure). Designed for use under
# the Monitor tool with persistent=true.
#
# Usage:
#   scripts/codeberg/pr-monitor.sh <pr-num> [<pr-num> ...]
#
# Exits early with code 2 if no PR numbers given or .codeberg.config
# is missing (the _lib.sh setup escalation).

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: pr-monitor.sh <pr-num> [<pr-num> ...]" >&2
  exit 2
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

PRS="$*"
declare -A prev final

while :; do
  done_count=0
  for pr in $PRS; do
    if [ -n "${final[$pr]:-}" ]; then
      done_count=$((done_count + 1))
      continue
    fi
    tmp=/tmp/cm_mon_${pr}.json
    if ! codeberg_curl GET "/pulls/$pr" > "$tmp" 2>/dev/null; then
      # Transient network error — try again next cycle.
      continue
    fi
    pr_state=$(jq -r '.state // "?"' "$tmp")
    merged=$(jq -r '.merged // false' "$tmp")
    sha=$(jq -r '.head.sha // "none"' "$tmp")
    if [ "$sha" = "none" ]; then
      continue
    fi
    # CI signal from Forgejo Actions run statuses — NOT the combined
    # commit-status endpoint, which ghosts as "pending" forever on this repo
    # (see project_codeberg_null_status_ghost). A SHA accumulates superseded
    # runs, so a re-run leaves a `cancelled`/`skipped` entry alongside the real
    # `success` — those must NOT count as failure. Aggregate per-job `status`:
    #   failure/error          -> failure (real red)
    #   running/waiting/pending -> pending (still going)
    #   >=1 success, none above -> success
    #   only cancelled/skipped  -> no-status (nothing real yet; keep waiting)
    sel=$(codeberg_curl GET "/actions/tasks?limit=50" \
          | jq -c --arg sha "${sha:0:7}" '[(.workflow_runs // .tasks // [])[] | select((.head_sha[0:7])==$sha)]' 2>/dev/null || echo '[]')
    n_fail=$(echo "$sel" | jq '[.[]|select(.status=="failure" or .status=="error")]|length')
    n_run=$(echo "$sel" | jq '[.[]|select(.status=="running" or .status=="waiting" or .status=="pending" or .status=="unknown")]|length')
    n_ok=$(echo "$sel" | jq '[.[]|select(.status=="success")]|length')
    if   [ "$n_fail" -gt 0 ]; then ci=failure
    elif [ "$n_run"  -gt 0 ]; then ci=pending
    elif [ "$n_ok"   -gt 0 ]; then ci=success
    else ci=no-status
    fi
    cur="PR#$pr: state=$pr_state merged=$merged ci=$ci sha=${sha:0:7}"
    if [ "$cur" != "${prev[$pr]:-}" ]; then
      echo "$cur"
      prev[$pr]="$cur"
    fi
    if [ "$merged" = "true" ] || [ "$pr_state" = "closed" ] || [ "$ci" = "failure" ]; then
      final[$pr]="$cur"
      done_count=$((done_count + 1))
    fi
  done
  # shellcheck disable=SC2086
  set -- $PRS
  if [ "$done_count" -eq "$#" ]; then
    break
  fi
  sleep 60
done

for pr in $PRS; do
  echo "FINAL: ${final[$pr]}"
done
