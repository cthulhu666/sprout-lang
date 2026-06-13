---
description: Monitor Codeberg PRs through CI to fast-forward merge, handling master-moved-during-CI by rebasing + regenerating the bootstrap seed + requeueing. Skips WIP/draft PRs. Escalates anything else.
---

# codeberg-merge

You are about to shepherd one or more open Codeberg PRs through to a
fast-forward merge. The only failure modes you handle autonomously are
the **master-moved-during-CI** race and its standard companion the
**bootstrap seed conflict**. Anything else (CI red, non-seed conflict,
force-push rejected, network error, missing config) is an **escalation**
— you print a structured line and stop working on that PR; remaining
PRs continue.

PRs that are **draft** or have a **WIP-prefixed title** are skipped
silently (not as escalations) — the author is still iterating and
automation must not interfere. See `feedback_skip_wip_prs_in_automation`
memory for rationale.

Full protocol context lives in
[`docs/codeberg-pr-workflow.md`](../../docs/codeberg-pr-workflow.md). This
skill is a thin orchestrator over the recipes there. Read it once if
anything below is unclear.

## Inputs

`$ARGUMENTS` is a space-separated list of PR numbers. If empty, query
the open PRs in the repo:

```sh
tea pr list --output simple 2>/dev/null | awk '/^[0-9]/ {print $1}'
```

## Setup (do this once, fail loud if it breaks)

```sh
if [ ! -f .codeberg.config ]; then
  echo "ESCALATION: setup — .codeberg.config missing — copy .codeberg.config.example and fill in your values"
  exit 1
fi
source .codeberg.config
TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" | grep token | cut -d: -f2 | tr -d ' ')
if [ -z "$TOKEN" ]; then
  echo "ESCALATION: setup — could not extract API token from $TEA_CONFIG_PATH — check tea login"
  exit 1
fi
API="https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO"
```

`$API`, `$TOKEN`, `$CODEBERG_OWNER`, `$CODEBERG_REPO`, `$TEA_CONFIG_PATH`
are then available for every recipe below.

## Per-PR state machine

For each PR number `$PR`:

### Step 1 — Snapshot + WIP gate

```sh
tmp=/tmp/cm_pr_${PR}.json
curl -sf -H "Authorization: token $TOKEN" "$API/pulls/$PR" > "$tmp" || {
  echo "ESCALATION: PR#$PR fetch failed"; continue
}
PR_STATE=$(jq -r '.state // "?"' "$tmp")
MERGED=$(jq -r '.merged // false' "$tmp")
DRAFT=$(jq -r '.draft // false' "$tmp")
TITLE=$(jq -r '.title // ""' "$tmp")
HEAD_SHA=$(jq -r '.head.sha // "none"' "$tmp")
HEAD_REF=$(jq -r '.head.ref // "none"' "$tmp")
```

**Skip checks (not escalations — log and `continue`):**

1. `DRAFT == true` → `PR#$PR: draft — skipping`
2. Title matches `^WIP:`, `^WIP ` (space), or `^\[WIP\]` (case-insensitive):
   ```sh
   if echo "$TITLE" | grep -qiE '^(WIP:|WIP |\[WIP\])'; then
     echo "PR#$PR: WIP-titled — skipping"; continue
   fi
   ```
3. `MERGED == true` → `PR#$PR: already merged`
4. `PR_STATE == closed` (and not merged) → `ESCALATION: PR#$PR closed without merging`

If none of the above triggered, proceed to Step 2.

### Step 2 — Queue the auto-merge

```sh
HTTP=$(curl -s -o /tmp/cm_queue_$PR.out -w "%{http_code}" \
  -X POST "$API/pulls/$PR/merge" \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"Do\":\"fast-forward-only\",\"head_commit_id\":\"$HEAD_SHA\",\"merge_when_checks_succeed\":true}")
```

- `HTTP=201` → queued. Proceed to Step 3.
- `HTTP=405` with body containing "checks have already succeeded" or
  similar → CI already green and master moved; go straight to Step 4
  (rebase) without polling.
- `HTTP=500` with empty body → master is already ahead at this moment;
  go straight to Step 4 (rebase).
- Any other code → `ESCALATION: PR#$PR queue failed HTTP=$HTTP body=<see /tmp/cm_queue_$PR.out>`.

### Step 3 — Wait for terminal state

Poll every 60s. On each iteration:

```sh
curl -sf -H "Authorization: token $TOKEN" "$API/pulls/$PR" > "$tmp"
PR_STATE=$(jq -r '.state // "?"' "$tmp")
MERGED=$(jq -r '.merged // false' "$tmp")
NEW_SHA=$(jq -r '.head.sha // "none"' "$tmp")
CI=$(curl -sf -H "Authorization: token $TOKEN" "$API/commits/$NEW_SHA/status" | jq -r '.state // "no-status"')
```

Print only when state changes vs last poll. Decide:

- `MERGED == true` → `PR#$PR: merged ✓` → done.
- `PR_STATE == closed` → `ESCALATION: PR#$PR closed without merge` → done.
- `CI == failure` → `ESCALATION: PR#$PR CI failed at $NEW_SHA` → done.
- `CI == success` && `MERGED == false` && `PR_STATE == open` → auto-merge
  did not fire (master moved during CI). Go to Step 4.
- Otherwise (`pending`, `no-status`) → `sleep 60`, loop.

### Step 4 — Rebase + regenerate seed + requeue

Autonomous-handling path. Refuse to start if the working tree is dirty.

```sh
if ! git diff-index --quiet HEAD --; then
  echo "ESCALATION: PR#$PR rebase blocked — working tree dirty"; continue
fi
ORIG_BRANCH=$(git branch --show-current)
git fetch origin master "$HEAD_REF" 2>&1 | tail -5
git checkout -B "$HEAD_REF" "origin/$HEAD_REF"

git rebase origin/master 2>&1 | tail -10
NON_SEED_CONFLICT=0
while [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; do
  CONFLICTS=$(git diff --name-only --diff-filter=U)
  if [ "$CONFLICTS" = "bootstrap/compile_driver.ll" ]; then
    git checkout --theirs bootstrap/compile_driver.ll
    git add bootstrap/compile_driver.ll
    git rebase --continue 2>&1 | tail -5
  else
    git rebase --abort
    git checkout "$ORIG_BRANCH"
    echo "ESCALATION: PR#$PR rebase has non-seed conflicts:"
    echo "$CONFLICTS"
    NON_SEED_CONFLICT=1
    break
  fi
done
[ "$NON_SEED_CONFLICT" = "1" ] && continue

# Rebase succeeded — regenerate the seed against the rebased tree.
rm -f build/compile_driver_bin_stage1
if ! scripts/memwatch.sh 4096 1 -- mise exec -- just refresh-seed; then
  git checkout "$ORIG_BRANCH"
  echo "ESCALATION: PR#$PR seed regeneration failed (see memwatch output)"; continue
fi

if ! git diff-index --quiet HEAD -- bootstrap/compile_driver.ll; then
  git add bootstrap/compile_driver.ll
  git commit -m "chore(bootstrap): refresh seed after rebase onto master"
fi

if ! git push --force-with-lease origin "$HEAD_REF" 2>&1 | tail -5; then
  git checkout "$ORIG_BRANCH"
  echo "ESCALATION: PR#$PR force-push-with-lease rejected — remote moved"; continue
fi

# Return to the working branch we started on.
git checkout "$ORIG_BRANCH"

# Fetch the new head sha, then loop back to Step 2 to requeue.
curl -sf -H "Authorization: token $TOKEN" "$API/pulls/$PR" > "$tmp"
HEAD_SHA=$(jq -r '.head.sha' "$tmp")
echo "PR#$PR: rebased + seed-refreshed, new head=${HEAD_SHA:0:7}, requeueing"
```

After requeue, return to Step 3 (poll for terminal state) with the new
SHA.

## Bounds

- **Per-PR max rebase attempts: 3.** If you've gone through Step 4 three
  times on the same PR without reaching `merged=true`, escalate:
  `ESCALATION: PR#$PR — 3 rebase attempts exhausted, master moves too fast`.
- **Poll interval: 60s.** Do not go below 30s (rate-limit risk).
- **Total wall-clock cap per PR: 4 hours.** Beyond that, escalate:
  `ESCALATION: PR#$PR — 4-hour cap reached`.

## Final summary

After all PRs reach a terminal state, print one line per PR plus a
total:

```sh
echo "DONE: codeberg-merge complete"
echo "  merged:     $MERGED_COUNT"
echo "  skipped:    $SKIPPED_COUNT   (draft/WIP/already-merged)"
echo "  escalated:  $ESCALATED_COUNT"
echo "  PRs handled: $PR_ARGS"
```

## Hard rules (do not violate)

- Never `git push --force` (always `--force-with-lease`).
- Never pass `--no-verify` to `git commit`.
- Never modify `git config`.
- Never delete a remote branch (`git push -d`, `tea pr close`).
- Never edit any file other than `bootstrap/compile_driver.ll` during
  the rebase (and only via `git checkout --theirs`).
- Never `cd` away from the repo root; all paths are relative to it.
- Wrap `just refresh-seed` with `scripts/memwatch.sh 4096 1 --` to cap
  RSS at 4 GB per project memory `feedback_memory_watchdog`.
- Skip draft / WIP-titled PRs per `feedback_skip_wip_prs_in_automation`
  — log "skipping", do not queue, do not rebase, do not poll.
- If `$ARGUMENTS` is empty AND `tea pr list` returns no PRs, print
  `DONE: no open PRs to handle` and exit 0 — not an escalation.

## Escalation contract

Every escalation line starts with `ESCALATION:` followed by `PR#$PR`
(or `setup` for pre-PR failures), then the reason. Supervising agents
and humans can grep `ESCALATION:` for triage. Halt work on the
escalated PR; continue with the rest of the list.
