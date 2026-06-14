#!/usr/bin/env bash
# Full Step 4 of the codeberg-merge skill executed as one script:
# rebase a PR's branch onto current master, handle the bootstrap-seed
# conflict via --theirs, regenerate the seed iff the rebase introduces
# any compiler-source diff vs master (bug #3 heuristic), run fmt,
# commit the seed if it changed, force-push (using a temp local branch
# if the canonical branch is checked out in a worktree), then requeue
# the auto-merge.
#
# Usage:
#   scripts/codeberg/pr-rebase.sh <pr-number>
#
# Exit codes:
#   0 — success; PR is force-pushed and auto-merge requeued.
#   1 — ESCALATION line printed to stdout; manual intervention needed.
#   2 — usage error.
#
# All progress and escalation lines go to stdout so a calling agent
# (or the Monitor tool wrapping this) can grep them.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: pr-rebase.sh <pr-number>" >&2
  exit 2
fi
PR=$1

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

escalate() {
  echo "ESCALATION: PR#$PR $*"
  # Best-effort restore of working state before exiting.
  if [ -n "${ORIG_BRANCH:-}" ] && [ "$(git branch --show-current)" != "$ORIG_BRANCH" ]; then
    git checkout "$ORIG_BRANCH" 2>/dev/null || true
  fi
  exit 1
}

# ---- Step 1: snapshot + gates -------------------------------------
tmp=/tmp/cm_rebase_${PR}.json
if ! codeberg_curl GET "/pulls/$PR" > "$tmp" 2>/dev/null; then
  echo "ESCALATION: PR#$PR fetch failed"
  exit 1
fi
PR_STATE=$(jq -r '.state // "?"' "$tmp")
MERGED=$(jq -r '.merged // false' "$tmp")
DRAFT=$(jq -r '.draft // false' "$tmp")
TITLE=$(jq -r '.title // ""' "$tmp")
HEAD_REF=$(jq -r '.head.ref // "none"' "$tmp")
HEAD_SHA=$(jq -r '.head.sha // "none"' "$tmp")

if [ "$DRAFT" = "true" ]; then echo "PR#$PR: draft — skipping"; exit 0; fi
if title_is_wip "$TITLE"; then echo "PR#$PR: WIP-titled — skipping"; exit 0; fi
if [ "$MERGED" = "true" ]; then echo "PR#$PR: already merged"; exit 0; fi
if [ "$PR_STATE" = "closed" ]; then escalate "closed without merging"; fi
if [ "$HEAD_REF" = "none" ]; then escalate "API returned no head.ref"; fi

# ---- Step 2: working-tree + worktree-collision pre-flight ---------
if ! git diff-index --quiet HEAD --; then
  escalate "rebase blocked — working tree dirty"
fi
ORIG_BRANCH=$(git branch --show-current)

# If the canonical branch is locked by a worktree, work on a temp
# local branch and push under the canonical name via refspec.
LOCAL_BRANCH=$HEAD_REF
PUSH_REFSPEC=$HEAD_REF
if git worktree list --porcelain | grep -q "^branch refs/heads/$HEAD_REF$"; then
  LOCAL_BRANCH="${HEAD_REF}-cm-rebase"
  PUSH_REFSPEC="${LOCAL_BRANCH}:${HEAD_REF}"
  echo "PR#$PR: branch $HEAD_REF locked by worktree — using temp local $LOCAL_BRANCH"
fi

# ---- Step 3: fetch + checkout + rebase ----------------------------
git fetch origin master "$HEAD_REF" >/dev/null 2>&1
git checkout -B "$LOCAL_BRANCH" "origin/$HEAD_REF" >/dev/null 2>&1

echo "PR#$PR: rebasing onto origin/master ($(git rev-parse origin/master | cut -c1-12))"
git rebase origin/master 2>&1 | tail -5 || true
NON_SEED_CONFLICT=0
while [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; do
  CONFLICTS=$(git diff --name-only --diff-filter=U)
  if [ -z "$CONFLICTS" ]; then
    git rebase --abort 2>/dev/null || true
    escalate "rebase stuck without conflicts (hook or git-internal error)"
    NON_SEED_CONFLICT=1
    break
  fi
  if [ "$CONFLICTS" = "bootstrap/compile_driver.ll" ]; then
    git checkout --theirs bootstrap/compile_driver.ll
    git add bootstrap/compile_driver.ll
    git rebase --continue 2>&1 | tail -3 || true
  else
    git rebase --abort 2>/dev/null || true
    echo "PR#$PR: rebase has non-seed conflicts:"
    echo "$CONFLICTS"
    NON_SEED_CONFLICT=1
    break
  fi
done
if [ "$NON_SEED_CONFLICT" = "1" ]; then
  escalate "non-seed rebase conflict (see file list above)"
fi
echo "PR#$PR: rebased; HEAD=$(git rev-parse HEAD | cut -c1-12)"

# ---- Step 4: bug-#3 heuristic + regen ----------------------------
COMPILER_DIFF=$(git diff --name-only origin/master HEAD -- \
  stdlib/compiler/ stdlib/prelude.sprout)
if [ -z "$COMPILER_DIFF" ]; then
  echo "PR#$PR: no compiler-source diff vs master — skipping seed regen"
else
  echo "PR#$PR: compiler source touched — regenerating seed (~minutes)"
  rm -f build/compile_driver_bin_stage1
  if ! scripts/memwatch.sh 4096 1 -- mise exec -- just refresh-seed >/tmp/cm_regen_${PR}.log 2>&1; then
    echo "PR#$PR: refresh-seed log tail:"
    tail -20 < /tmp/cm_regen_${PR}.log || true
    escalate "seed regeneration failed (full log: /tmp/cm_regen_${PR}.log)"
  fi
  echo "PR#$PR: seed regeneration ok"
fi

# ---- Step 5: fmt + commit if seed changed ------------------------
if [ -n "$COMPILER_DIFF" ]; then
  mise exec -- just fmt >/dev/null 2>&1 || echo "PR#$PR: fmt non-zero (continuing)"
fi
if ! git diff-index --quiet HEAD -- bootstrap/compile_driver.ll; then
  git add bootstrap/compile_driver.ll
  git commit -m "chore(bootstrap): refresh seed after rebase onto master" >/dev/null
  echo "PR#$PR: committed seed refresh"
fi

# ---- Step 6: force-push --------------------------------------------
if ! git push --force-with-lease origin "$PUSH_REFSPEC" 2>&1 | tail -3; then
  escalate "force-push-with-lease rejected — remote moved or branch locked"
fi
echo "PR#$PR: pushed"

# ---- Step 7: restore working branch -------------------------------
if [ "$ORIG_BRANCH" != "$LOCAL_BRANCH" ]; then
  git checkout "$ORIG_BRANCH" >/dev/null 2>&1 || true
fi

# ---- Step 8: refetch new head + requeue auto-merge ----------------
sleep 3
codeberg_curl GET "/pulls/$PR" > "$tmp" 2>/dev/null
NEW_HEAD=$(jq -r '.head.sha' "$tmp")
echo "PR#$PR: new head ${NEW_HEAD:0:12}; requeueing auto-merge"

HTTP=$(curl -s -o /tmp/cm_requeue_${PR}.out -w "%{http_code}" \
  -X POST "$API/pulls/$PR/merge" \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"Do\":\"fast-forward-only\",\"head_commit_id\":\"$NEW_HEAD\",\"merge_when_checks_succeed\":true}")
case "$HTTP" in
  201)
    echo "PR#$PR: auto-merge queued (HTTP 201) — ff-only, fires when CI passes"
    ;;
  405)
    # Opaque 405 — could be "queue already armed" (fine) or "not
    # ff-able" (would need another rebase). Check ancestry to decide.
    if git merge-base --is-ancestor origin/master "origin/$HEAD_REF" 2>/dev/null; then
      echo "PR#$PR: queue HTTP 405 but branch is ff-able — likely idempotent rejection (latch already armed). Trusting it."
    else
      escalate "queue HTTP 405 and branch is not ff-able vs master — another rebase needed"
    fi
    ;;
  *)
    BODY=$(cat /tmp/cm_requeue_${PR}.out 2>/dev/null || echo '(no body)')
    escalate "requeue failed HTTP=$HTTP body=$BODY"
    ;;
esac

echo "PR#$PR: DONE (push complete, requeue submitted, monitor separately)"
