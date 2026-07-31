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

# Set CODEBERG_LIB_NO_SETUP=1 to source only the pure helper functions below
# (e.g. from unit tests) without requiring config/token/network.
if [ -z "${CODEBERG_LIB_NO_SETUP:-}" ]; then
  # Locate .codeberg.config. Prefer the CWD (repo root); fall back to the MAIN
  # working tree. The config is gitignored, so a fresh git worktree does NOT carry
  # it — but every worktree shares one main checkout where it lives. Without this
  # fallback the PR scripts escalate "config missing" from any worktree, and callers
  # then hand-roll `tea pr create`, which ITSELF cannot create a PR from a worktree
  # (worktree .git is a pointer file tea's go-git cannot read) — the exact recurring
  # failure these scripts exist to prevent.
  CODEBERG_CONFIG=.codeberg.config
  if [ ! -f "$CODEBERG_CONFIG" ]; then
    _main_wt=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)/.." 2>/dev/null && pwd -P)
    if [ -n "${_main_wt:-}" ] && [ -f "$_main_wt/.codeberg.config" ]; then
      CODEBERG_CONFIG="$_main_wt/.codeberg.config"
    fi
  fi
  if [ ! -f "$CODEBERG_CONFIG" ]; then
    echo "ESCALATION: setup — .codeberg.config missing (looked in the CWD and the main working tree) — copy .codeberg.config.example and fill in your values" >&2
    exit 1
  fi

  # shellcheck disable=SC1091
  source "$CODEBERG_CONFIG"

  TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" 2>/dev/null \
    | grep token \
    | cut -d: -f2 \
    | tr -d ' ')
  if [ -z "${TOKEN:-}" ]; then
    echo "ESCALATION: setup — could not extract API token from $TEA_CONFIG_PATH — check tea login" >&2
    exit 1
  fi

  API="https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO"
fi

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
  echo "${prefix}BASE_REF=$(jq -r '.base.ref // "master"' "$tmp")"
  echo "${prefix}MERGEABLE=$(jq -r '.mergeable // "?"' "$tmp")"
}

# Returns 0 if the title looks like a WIP marker, 1 otherwise.
title_is_wip() {
  echo "$1" | grep -qiE '^(WIP:|WIP |\[WIP\])'
}

# Aggregate a Forgejo Actions `/actions/tasks` JSON payload into one CI signal
# for <sha>. Args: <tasks-json> <sha>. Echoes: failure | pending | success |
# no-status. Pure (no network) so it is unit-testable — see pr-monitor-test.sh.
#
# Gates ONLY on the `test` job (the DoD suite). `setup` is a `needs:` dependency
# of `test`, so there is a window where `setup` is green and `test` has not
# spawned; aggregating over ALL jobs reads that as false `success` (it merged
# PR#121 prematurely, 2026-07-03; see feedback_codeberg_merge_reverify_ci).
# Restricting to `test` makes that window read as no-status (keep waiting).
ci_from_tasks() {
  local json=$1 sha=$2 sel n_fail n_run n_ok
  sel=$(printf '%s' "$json" | jq -c --arg sha "${sha:0:7}" \
        '[(.workflow_runs // .tasks // [])[] | select((.head_sha[0:7])==$sha and .name=="test")]' 2>/dev/null || echo '[]')
  n_fail=$(printf '%s' "$sel" | jq '[.[]|select(.status=="failure" or .status=="error")]|length')
  n_run=$(printf '%s' "$sel"  | jq '[.[]|select(.status=="running" or .status=="waiting" or .status=="pending" or .status=="unknown")]|length')
  n_ok=$(printf '%s' "$sel"   | jq '[.[]|select(.status=="success")]|length')
  if   [ "$n_fail" -gt 0 ]; then echo failure
  elif [ "$n_run"  -gt 0 ]; then echo pending
  elif [ "$n_ok"   -gt 0 ]; then echo success
  else echo no-status; fi
}

# Authoritative pre-merge CI gate. Returns 0 iff the combined commit status for
# <sha> is exactly "success" (i.e. every required context is green).
#
# ALWAYS call this immediately before POSTing a merge. Do NOT merge on the
# pr-monitor.sh `ci=success` event alone: that is a heuristic over Actions runs
# and can fire early in the `setup`-green / `test`-not-spawned window (it merged
# PR#121 prematurely, 2026-07-03; see feedback_codeberg_merge_reverify_ci). The
# combined-status endpoint returns "pending" while any required check is
# unfinished, so requiring == "success" here cannot be fooled by that window,
# nor by the null-state ghost (null != success) that makes the endpoint
# unreliable as a *read* signal — see project_codeberg_null_status_ghost.
ci_is_green() {
  local sha=$1
  local state
  state=$(codeberg_curl GET "/commits/$sha/status" | jq -r '.state // "unknown"' 2>/dev/null)
  [ "$state" = "success" ]
}

# Pure: succeeds (0) iff an HTTP status from the ff-merge POST means the merge
# actually landed — Gitea returns 200 (some builds 201). EVERYTHING else means
# "did not merge". Critically this includes 500, which THIS Gitea returns for a
# non-fast-forwardable fast-forward-only merge (not the 405/409 you'd expect;
# observed on PR#271, 2026-07-27). So a caller must NEVER treat a non-2xx code
# as a fatal/unknown error on its own — it must diagnose fast-forwardability
# (git merge-base --is-ancestor) to route between retry, rebase, and escalate.
# Kept pure/offline for unit testing — see pr-babysit-test.sh.
is_merge_success() {
  [ "${1:-}" = "200" ] || [ "${1:-}" = "201" ]
}

# Pure git: succeeds (0) iff <maybe-ancestor> is an ancestor of (or equal to)
# <descendant>. Both must be locally-available refs. Wraps the single git
# predicate the stacked-PR base-retarget decision turns on (see pr-babysit.sh),
# so that decision is regression-testable against a throwaway repo without any
# network/API — see pr-babysit-test.sh.
ref_is_ancestor() {
  git merge-base --is-ancestor "$1" "$2" 2>/dev/null
}

# Build one pr-monitor event line. Args: <pr> <state> <merged> <ci> <sha>.
# Emits the FULL <sha> unchanged: the caller feeds this line's sha= field into
# the ff-merge POST's head_commit_id, which requires the full 40-char SHA — a
# truncated SHA is rejected HTTP 409 "head out of date" even when genuinely
# fast-forwardable (see project_codeberg_ffmerge_needs_full_sha). Kept pure so
# the full-SHA contract is regression-testable offline — see pr-monitor-test.sh.
format_monitor_line() {
  printf 'PR#%s: state=%s merged=%s ci=%s sha=%s' "$1" "$2" "$3" "$4" "$5"
}
