#!/usr/bin/env bash
# Claude Code Stop hook: spawn an independent DoD reviewer sub-agent after each commit.
#
# Fires on every agent stop. Checks if HEAD has already been reviewed (per-worktree
# marker file). If not, spawns `claude -p` with a fixed, hardcoded prompt — the main
# agent cannot influence the reviewer's prompt or verdict.
#
# Worktree safety: marker files live under $(git rev-parse --absolute-git-dir), which
# is per-worktree (.git/worktrees/<name>/ for linked worktrees). Parallel agents in
# separate worktrees each get their own marker namespace with no shared state.
set -euo pipefail

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("cwd","."))')
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id","unknown"))')

cd "$CWD"

# Locate repo root and git-dir (--absolute-git-dir avoids relative .git in main worktree).
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
GIT_DIR=$(git rev-parse --absolute-git-dir 2>/dev/null) || exit 0
cd "$REPO_ROOT"

# Nothing to review if no commits exist yet.
COMMIT=$(git rev-parse HEAD 2>/dev/null) || exit 0

# Already reviewed this commit in this worktree? Skip.
MARKER="$GIT_DIR/dod-reviewed-$COMMIT"
[ -f "$MARKER" ] && exit 0

# Spawn independent reviewer with a fixed prompt the agent cannot influence.
REVIEWER_PROMPT="You are an independent DoD reviewer for the sprout_lang project.
Your task: verify the most recent git commit meets all applicable Definition of Done criteria.

Steps:
1. Read $REPO_ROOT/AGENTS.md — find the numbered DoD criteria (§Definition of Done)
2. Run: git -C $REPO_ROOT diff HEAD~1..HEAD --name-only   (see which files changed)
3. Determine which DoD items apply based on changed file paths:
   - Any *.sprout or *.spr file     → #4 (fmt), #5 (full test suite)
   - stdlib/compiler/*.sprout or stdlib/*.sprout → also #7 (smoke-shapes), #8 (bundle-smoke), #9 (seed)
   - runtime/sprout_runtime.c       → also #10 (APPROVED_BUILTINS), #11 (example canary)
   - Always applicable              → #1 (impl complete), #2 (tests from DoR pass), #3 (docs), #13 (self-review)
4. For each applicable item, check whether it was satisfied. Evidence sources:
   - Test results : /tmp/sprout_test_${SESSION_ID}.txt  (this agent's session)
   - Seed freshness: git -C $REPO_ROOT diff HEAD~1..HEAD -- bootstrap/compile_driver.ll
   - Smoke/bundle/canary: look in the test output file above, or re-run the lightweight checks
5. Output ONLY valid JSON on the very last line — nothing after it:
   {\"pass\": true, \"issues\": []}
   or
   {\"pass\": false, \"issues\": [\"item N: specific reason\", ...]}"

RESULT=$(claude -p "$REVIEWER_PROMPT" 2>/dev/null | tail -1) || {
  # claude CLI unavailable — fail open so human terminal commits are not blocked.
  exit 0
}

PASS=$(printf '%s' "$RESULT" | python3 -c \
  'import sys,json; d=json.loads(sys.stdin.read()); print("true" if d.get("pass") else "false")' \
  2>/dev/null || echo "false")

if [ "$PASS" = "true" ]; then
  touch "$MARKER"
  exit 0
fi

ISSUES=$(printf '%s' "$RESULT" | python3 -c \
  'import sys,json; d=json.loads(sys.stdin.read()); print("; ".join(d.get("issues", ["(no detail)"])))' \
  2>/dev/null || printf '%s' "$RESULT")

printf '{"decision":"block","reason":"DoD reviewer found issues — fix before stopping:\n%s"}\n' "$ISSUES"
exit 0
