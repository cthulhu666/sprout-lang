#!/usr/bin/env bash
# Claude Code PreToolUse hook: intercept `git commit` (all flavors) and
# require an explicit DoD acknowledgement before allowing the commit.
#
# Acknowledgement mechanism (γ.2, tree-hash-bound):
#   1. Agent verifies DoD criteria for the staged changes.
#   2. Agent runs:  just dod-ack   (writes `git write-tree` hash to .git/dod-ack)
#   3. Agent retries the commit.
# The hook re-computes git write-tree on the staged index and compares.
# Same tree as the ack → commit proceeds.  Different tree → DENY with checklist.
#
# Why tree-hash, not session/PID/time: the ack semantically attests to "I
# verified THIS staged content."  Tree hash is the only identifier that
# captures that exactly.  Amend-message-only commits don't change the tree,
# so they reuse the ack for free.

set -euo pipefail

# Read Claude Code hook input (JSON on stdin).
INPUT=$(cat)

# Only act on Bash tool calls whose command runs `git commit`.
# Matches: git commit, git -c k=v commit, git commit -m ..., --amend, --fixup, etc.
# Substring-match the command after extracting it from JSON.
CMD=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("command",""))' 2>/dev/null || true)
if [[ -z "$CMD" ]]; then exit 0; fi

# Catch all common ways to spell `git commit`. Use word-boundary-ish anchoring.
if ! printf '%s' "$CMD" | grep -qE '(^|[ \t&|;`(])git[ \t]+([^ \t]+[ \t]+)*commit($|[ \t&|;`)])'; then
  exit 0
fi

# We're handling a `git commit`. Locate the repo root and check the ack.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" ]]; then exit 0; fi
cd "$REPO_ROOT"

TREE_HASH=$(git write-tree 2>/dev/null || true)

# --- Seed gate (DoD #9): compiler source staged → seed must be refreshed or verified ---
COMPILER_STAGED=$(git diff --cached --name-only 2>/dev/null | grep -E '^stdlib/compiler/[^/]+\.sprout$|^stdlib/[^/]+\.sprout$' || true)
if [[ -n "$COMPILER_STAGED" ]]; then
  SEED_STAGED=$(git diff --cached --name-only 2>/dev/null | grep -F 'bootstrap/compile_driver.ll' || true)
  FP_ACK_FILE=".git/seed-fp-ack"
  SEED_OK=false
  if [[ -n "$SEED_STAGED" ]]; then
    SEED_OK=true
  elif [[ -n "$TREE_HASH" && -f "$FP_ACK_FILE" && "$(cat "$FP_ACK_FILE" 2>/dev/null)" == "$TREE_HASH" ]]; then
    SEED_OK=true
  fi
  if [[ "$SEED_OK" != "true" ]]; then
    cat >&2 <<'SEED_MSG'
BLOCKED: Compiler-source files are staged but bootstrap/compile_driver.ll has not been refreshed (DoD #9).

If the compiler change affects IR output:
  scripts/memwatch.sh 4096 1 -- just refresh-seed
  git add bootstrap/compile_driver.ll
  <retry the commit>

If the IR output is unchanged (verify first, then ack):
  scripts/memwatch.sh 4096 1 -- just verify-bootstrap-fixed-point
  just seed-fp-ack
  <retry the commit>

SEED_MSG
    exit 2
  fi
fi

ACK_FILE=".git/dod-ack"

if [[ -n "$TREE_HASH" && -f "$ACK_FILE" && "$(cat "$ACK_FILE" 2>/dev/null)" == "$TREE_HASH" ]]; then
  exit 0
fi

# Deny with the DoD reminder.
cat >&2 <<'MSG'
BLOCKED: DoD acknowledgement required before `git commit`.

Verify ALL applicable criteria in AGENTS.md §"Definition of Done" for the staged changes,
then acknowledge:

  just dod-ack && <retry the original `git commit ...`>

MSG
exit 2
