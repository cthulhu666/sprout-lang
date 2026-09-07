#!/usr/bin/env bash
# Claude Code PreToolUse hook: block `git commit` when compiler sources are staged
# but bootstrap/compile_driver.ll has not been refreshed (DoD #9).
#
# This is a mechanical data-integrity check only — it does NOT enforce the full DoD
# checklist.
#
# Worktree safety: ack files live under $(git rev-parse --absolute-git-dir), which is
# per-worktree (.git/worktrees/<name>/ for linked worktrees). Parallel agents in
# separate worktrees each get their own ack namespace with no shared state.
set -euo pipefail

INPUT=$(cat)

CMD=$(printf '%s' "$INPUT" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("command",""))' \
  2>/dev/null || true)
if [[ -z "$CMD" ]]; then exit 0; fi

# Only act when the command is a git commit.
if ! printf '%s' "$CMD" | grep -qE '(^|[ \t&|;`(])git[ \t]+([^ \t]+[ \t]+)*commit($|[ \t&|;`)])'; then
  exit 0
fi

# The hook's cwd is the project dir, but commits are made in a worktree — as
# `cd <dir> && git commit` or `git -C <dir> commit`. Resolve the target from the command
# itself, or the gate inspects a tree with nothing staged and passes everything.
TARGET=$(printf '%s' "$CMD" | sed -n -E "s/.*git[[:space:]]+-C[[:space:]]+[\"']?([^\"'[:space:]]+).*/\1/p" | head -1)
if [[ -z "$TARGET" ]]; then
  TARGET=$(printf '%s' "$CMD" | sed -n -E "s/^[[:space:]]*cd[[:space:]]+[\"']?([^\"'&|;[:space:]]+).*/\1/p" | head -1)
fi
if [[ -n "$TARGET" && -d "$TARGET" ]]; then cd "$TARGET"; fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" ]]; then exit 0; fi
cd "$REPO_ROOT"

# --absolute-git-dir avoids relative .git in the main worktree.
GIT_DIR=$(git rev-parse --absolute-git-dir 2>/dev/null || true)
if [[ -z "$GIT_DIR" ]]; then exit 0; fi

TREE_HASH=$(git write-tree 2>/dev/null || true)

# Seed gate (DoD #9): compiler source staged → seed must be refreshed or verified.
COMPILER_STAGED=$(git diff --cached --name-only 2>/dev/null \
  | grep -E '^stdlib/compiler/[^/]+\.sprout$|^stdlib/[^/]+\.sprout$' || true)
if [[ -n "$COMPILER_STAGED" ]]; then
  SEED_STAGED=$(git diff --cached --name-only 2>/dev/null | grep -F 'bootstrap/compile_driver.ll' || true)
  FP_ACK_FILE="$GIT_DIR/seed-fp-ack"
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

exit 0
