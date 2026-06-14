#!/usr/bin/env bash
# Shared helpers for scripts/codeberg/*.sh. Source this from other
# scripts in this directory:
#
#   SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
#   source "$SCRIPT_DIR/_lib.sh"
#
# After sourcing, $TOKEN, $API, $CODEBERG_OWNER, $CODEBERG_REPO,
# $TEA_CONFIG_PATH are set. Callers must run from the repo root
# (where .codeberg.config lives).
#
# This file does NOT `set -e` — callers may want to handle errors
# themselves. Setup failures are still loud (stderr + exit 1) because
# nothing useful can happen without config.

if [ ! -f .codeberg.config ]; then
  echo "ESCALATION: setup — .codeberg.config missing — copy .codeberg.config.example and fill in your values" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .codeberg.config

TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" 2>/dev/null \
  | grep token \
  | cut -d: -f2 \
  | tr -d ' ')
if [ -z "${TOKEN:-}" ]; then
  echo "ESCALATION: setup — could not extract API token from $TEA_CONFIG_PATH — check tea login" >&2
  exit 1
fi

API="https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO"

# Wrapper around `curl` for Codeberg API calls. Adds auth header and
# silences progress output. Caller specifies HTTP method, path
# (starting with /), and any extra curl args.
#
# Examples:
#   codeberg_curl GET /pulls/36 > /tmp/pr36.json
#   codeberg_curl POST /pulls/36/merge -H 'Content-Type: application/json' -d "$BODY"
codeberg_curl() {
  local method=$1
  local path=$2
  shift 2
  curl -s -H "Authorization: token $TOKEN" -X "$method" "$API$path" "$@"
}

# Atomic snapshot of one PR's relevant state. Echoes a single
# `key=value` line per field — designed for `eval "$(pr_snapshot N)"`
# in callers. Sets prefix on each var name so multiple snapshots don't
# collide in the caller's namespace.
#
# Example:
#   eval "$(pr_snapshot 36 PR36_)"
#   echo "$PR36_STATE $PR36_MERGED $PR36_HEAD_SHA"
pr_snapshot() {
  local pr=$1
  local prefix=${2:-PR_}
  local tmp=/tmp/cm_${pr}_snap.json
  if ! codeberg_curl GET "/pulls/$pr" > "$tmp" 2>/dev/null; then
    echo "${prefix}FETCH_OK=0"
    return 1
  fi
  echo "${prefix}FETCH_OK=1"
  echo "${prefix}STATE=$(jq -r '.state // "?"' "$tmp")"
  echo "${prefix}MERGED=$(jq -r '.merged // false' "$tmp")"
  echo "${prefix}DRAFT=$(jq -r '.draft // false' "$tmp")"
  echo "${prefix}TITLE=$(jq -r '.title // "" | @sh' "$tmp")"
  echo "${prefix}HEAD_SHA=$(jq -r '.head.sha // "none"' "$tmp")"
  echo "${prefix}HEAD_REF=$(jq -r '.head.ref // "none"' "$tmp")"
  echo "${prefix}MERGEABLE=$(jq -r '.mergeable // "?"' "$tmp")"
}

# Returns 0 if the title looks like a WIP marker, 1 otherwise.
title_is_wip() {
  echo "$1" | grep -qiE '^(WIP:|WIP |\[WIP\])'
}
