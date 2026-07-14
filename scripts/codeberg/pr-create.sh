#!/usr/bin/env bash
# Create a PR from the current branch to `master` (or a given base) on Codeberg,
# idempotently. Prints the PR URL.
#
# WHY this exists instead of `tea pr create`: `tea pr create` (and any `tea`
# command with `--repo`) ALWAYS fails from a git worktree — the worktree's `.git`
# is a pointer file tea's go-git library cannot read. This repo is developed in
# worktrees, so PR creation must go through the REST API. This script wraps that
# so it is one command, not hand-rolled curl/jq (which is easy to get wrong:
# forgetting to check for an existing PR, mis-escaping the body, forgetting to
# push first — all mistakes made on 2026-07-14).
#
# Usage:
#   scripts/codeberg/pr-create.sh <title> <body-file> [base]
#
# Requires .codeberg.config at the repo root (see .codeberg.config.example) and a
# `tea login` for codeberg.org (the API token is read from the tea config).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."            # repo root, where .codeberg.config lives
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_lib.sh"      # sets $TOKEN $API; provides codeberg_curl

TITLE="${1:?usage: pr-create.sh <title> <body-file> [base]}"
BODY_FILE="${2:?usage: pr-create.sh <title> <body-file> [base]}"
BASE="${3:-master}"
[ -f "$BODY_FILE" ] || { echo "pr-create: body file not found: $BODY_FILE" >&2; exit 1; }

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "$BASE" ]; then
  echo "pr-create: refusing to open a PR from '$BASE' onto itself" >&2; exit 1
fi

# Idempotent: if an open PR already exists for this branch, print it and stop.
# (Skipping this check is what caused a confused duplicate-create attempt.)
existing=$(codeberg_curl GET "/pulls?state=open&limit=50" \
  | jq -r --arg b "$BRANCH" '.[] | select(.head.ref==$b) | .html_url' | head -1)
if [ -n "$existing" ]; then
  echo "pr-create: an open PR already exists for '$BRANCH': $existing"
  exit 0
fi

# Push the branch first (a PR needs a remote head). Safe to re-run.
git push -u origin "HEAD:$BRANCH"

# Build the payload with --rawfile so the body's newlines/markdown are escaped
# correctly (hand-substituting the body via $(cat ...) trips jq on control chars).
payload=$(jq -n \
  --arg t "$TITLE" \
  --rawfile b "$BODY_FILE" \
  --arg h "$BRANCH" \
  --arg base "$BASE" \
  '{title:$t, body:$b, head:$h, base:$base}')

resp=$(codeberg_curl POST "/pulls" -H 'Content-Type: application/json' -d "$payload")
url=$(printf '%s' "$resp" | jq -r '.html_url // empty')
if [ -n "$url" ]; then
  echo "pr-create: created $url"
else
  echo "pr-create: FAILED — $(printf '%s' "$resp" | jq -r '.message // tostring')" >&2
  exit 1
fi
