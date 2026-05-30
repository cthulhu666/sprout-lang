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
ACK_FILE=".git/dod-ack"

if [[ -n "$TREE_HASH" && -f "$ACK_FILE" && "$(cat "$ACK_FILE" 2>/dev/null)" == "$TREE_HASH" ]]; then
  exit 0
fi

# Deny with the DoD reminder.
cat >&2 <<'MSG'
BLOCKED: DoD acknowledgement required before `git commit`.

Confirm the relevant criteria from AGENTS.md are met for the staged changes.

Definition of Ready (entry conditions — should already be true):
  - Design approved by user when required
  - Failing test exists for new features (TDD)
  - Regression test exists & reproduces the defect for bug fixes
  - Coverage-gap tests drafted for edits to files with gaps

Definition of Done (exit conditions — verify now):
  - Implementation complete; DoR tests now pass
  - Docs & spec in sync with the implementation
  - `mise exec -- just fmt` run on changed .sprout/.spr files
  - `mise exec -- just test` passes (required for code/semantics changes)
  - `mise exec -- just compile-examples-stage1` passes
  - Compiler-source changes: smoke shapes pass + bundle smoke passes
  - Runtime changes: new C builtins listed in runtime/APPROVED_BUILTINS
  - Bootstrap/runtime changes: example canary RUNS without crash
  - Skipped tests treated as gaps unless user explicitly accepted them

To proceed, after verifying:
  just dod-ack && <retry the original `git commit ...`>

MSG
exit 2
