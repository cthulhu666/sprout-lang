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
    ci=$(codeberg_curl GET "/commits/$sha/status" | jq -r '.state // "no-status"')
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
