---
name: codeberg-merge
description: Shepherd one or more open Codeberg PRs through CI to a fast-forward merge. Handles the standard "master moved during CI → rebase + regenerate the bootstrap seed → requeue" cycle autonomously. Skips draft / WIP-titled PRs. Escalates anything else (CI failure, non-seed conflict, force-push rejected, network error, missing config) via structured `ESCALATION:` lines. Invoke when the user asks to merge, monitor, or auto-merge open PRs on this Codeberg repo.
---

# codeberg-merge

You are about to shepherd one or more open Codeberg PRs through to a
fast-forward merge. Only two failure modes are handled autonomously:
the **master-moved-during-CI race** and the **bootstrap seed conflict**
it produces on rebase. Everything else is an **escalation** — print a
structured `ESCALATION:` line, stop work on that PR, continue with the
others.

PRs that are **draft** or have a **WIP-prefixed title** are skipped
silently (log "skipping", not as escalations). The author is still
iterating; automation must not interfere. Rationale lives in memory at
`feedback_skip_wip_prs_in_automation.md`.

Full protocol context lives in
[`docs/codeberg-pr-workflow.md`](../../../docs/codeberg-pr-workflow.md).
This skill is a thin orchestrator over the recipes there.

## Inputs

`$ARGUMENTS` is a space-separated list of PR numbers. If empty, query
open PRs:

```sh
tea pr list --output simple 2>/dev/null | awk '/^[0-9]/' | cut -d' ' -f1
```

> **Skill-template gotcha**: the harness substitutes any bare `$N` digit
> token (`$1`, `$2`, …) in this skill body with the Nth token of
> `$ARGUMENTS` when the skill is rendered. **Never write `$1` / `$2` /
> etc. in this file** — not as a shell positional, not as `awk '{print
> $1}'`, not as `${1}`. Use named variables, `$@`, `cut -d' ' -f1`, or
> any other form without a bare digit.

## Setup (do this once; fail loud if it breaks)

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

Skip checks (not escalations — log "skipping" and `continue`):

1. `DRAFT == true` → `PR#$PR: draft — skipping`.
2. Title matches `^WIP:`, `^WIP ` (with space), or `^\[WIP\]`
   (case-insensitive):
   ```sh
   if echo "$TITLE" | grep -qiE '^(WIP:|WIP |\[WIP\])'; then
     echo "PR#$PR: WIP-titled — skipping"; continue
   fi
   ```
3. `MERGED == true` → `PR#$PR: already merged`.
4. `PR_STATE == closed` and `MERGED == false` →
   `ESCALATION: PR#$PR closed without merging`.

If none of the above trigger, proceed to Step 2.

### Step 2 — Queue the auto-merge

```sh
HTTP=$(curl -s -o /tmp/cm_queue_$PR.out -w "%{http_code}" \
  -X POST "$API/pulls/$PR/merge" \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"Do\":\"fast-forward-only\",\"head_commit_id\":\"$HEAD_SHA\",\"merge_when_checks_succeed\":true}")
```

- `HTTP=201` → queued, proceed to Step 3.
- `HTTP=405` with body containing "checks have already succeeded" → CI
  already green and master moved; go straight to Step 4 (rebase) without
  polling.
- `HTTP=500` with empty body → master is already ahead at this moment;
  go straight to Step 4 (rebase).
- Any other code → `ESCALATION: PR#$PR queue failed HTTP=$HTTP body=<see /tmp/cm_queue_$PR.out>`.

### Step 3 — Wait for a terminal state via Monitor

**Do not poll in a foreground bash loop** — the Bash tool's per-call
cap (10 min) is far shorter than a typical CI run (40-50 min) and shell
state does not persist across invocations. Use the `Monitor` tool with
`persistent: true` and let each state change arrive as a conversation
event you react to in normal turn cycles.

Launch the monitor with the file-mediated jq pattern from
`docs/codeberg-pr-workflow.md` §"Monitor CI to completion", adapted to
your `$ARGUMENTS`. The monitor must:

- Emit one line per state change per PR, in the form
  `PR#<N>: state=<X> merged=<Y> ci=<Z> sha=<S>`.
- Exit on its own once every PR has reached a terminal state (merged /
  closed / ci=failure), so the monitor naturally ends.
- Use `sleep 60` between polls — never below 30s (rate-limit risk).
- Source `.codeberg.config` and extract `$TOKEN` inside the monitor
  script (the Monitor command runs in a fresh shell).
- Use `set -u` carefully — associative arrays with `${var:-}` defaults
  to avoid "unbound" exits mid-loop.

The minimal command body (template — the agent fills in the PR list):

```sh
source .codeberg.config
TOKEN=$(grep -A2 'name: codeberg.org' "$TEA_CONFIG_PATH" | grep token | cut -d: -f2 | tr -d ' ')
API="https://codeberg.org/api/v1/repos/$CODEBERG_OWNER/$CODEBERG_REPO"
PRS="29 32"  # REPLACE with your PR numbers, space-separated
declare -A prev final
while :; do
  done_count=0
  for pr in $PRS; do
    [ -n "${final[$pr]:-}" ] && { done_count=$((done_count + 1)); continue; }
    tmp=/tmp/cm_mon_${pr}.json
    curl -sf -H "Authorization: token $TOKEN" "$API/pulls/$pr" > "$tmp" 2>/dev/null || continue
    pr_state=$(jq -r '.state // "?"' "$tmp")
    merged=$(jq -r '.merged // false' "$tmp")
    sha=$(jq -r '.head.sha // "none"' "$tmp")
    [ "$sha" = "none" ] && continue
    ci=$(curl -sf -H "Authorization: token $TOKEN" "$API/commits/$sha/status" | jq -r '.state // "no-status"')
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
  set -- $PRS
  [ "$done_count" -eq "$#" ] && break
  sleep 60
done
```

For each event line you receive, decide:

- `merged=true` → `PR#$PR: merged ✓`, mark done.
- `state=closed` && `merged=false` → `ESCALATION: PR#$PR closed without merge`, mark done.
- `ci=failure` → `ESCALATION: PR#$PR CI failed at $SHA`, mark done.
- `ci=success` && `state=open` && `merged=false` → auto-merge did not
  fire (master moved during CI). Use `TaskStop` on the monitor, run
  Step 4 (rebase + requeue), then relaunch the monitor for any PRs
  not yet done.
- Otherwise (`ci=pending`, `ci=no-status`) → no action; the monitor
  continues.

Wall-clock cap: 4 hours total. Beyond that, `TaskStop` the monitor and
escalate any still-open PRs with
`ESCALATION: PR#$PR — 4-hour cap reached`.

### Step 4 — Rebase + regenerate seed + requeue

Autonomous-handling path for the master-moved race. Refuse to start if
the working tree is dirty.

```sh
if ! git diff-index --quiet HEAD --; then
  echo "ESCALATION: PR#$PR rebase blocked — working tree dirty"; continue
fi
ORIG_BRANCH=$(git branch --show-current)
git fetch origin master "$HEAD_REF" 2>&1 | tail -5
git checkout -B "$HEAD_REF" "origin/$HEAD_REF"

REBASE_OUT=$(git rebase origin/master 2>&1)
echo "$REBASE_OUT" | tail -10
NON_SEED_CONFLICT=0
while [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; do
  CONFLICTS=$(git diff --name-only --diff-filter=U)
  if [ -z "$CONFLICTS" ]; then
    # Stuck mid-rebase with no conflicting files — hook failure,
    # lock contention, or git-internal error. Not the case we handle.
    git rebase --abort 2>/dev/null
    git checkout "$ORIG_BRANCH"
    echo "ESCALATION: PR#$PR rebase stuck without conflicts — see git output above"
    NON_SEED_CONFLICT=1
    break
  fi
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

# Rebase succeeded — decide whether seed regen is actually needed.
# Only changes to compiler source (stdlib/compiler/** or stdlib/prelude.sprout)
# affect the bootstrap seed. Tooling-only PRs (docs, .claude/, .gitignore,
# example fixtures, runtime/* without ABI impact) can reuse master's
# already-CI-validated seed, saving 30-60 min of unnecessary self-compile.
COMPILER_DIFF=$(git diff --name-only origin/master HEAD -- \
  stdlib/compiler/ stdlib/prelude.sprout)
if [ -z "$COMPILER_DIFF" ]; then
  echo "PR#$PR: rebase has no compiler-source diff vs master — skipping seed regen"
else
  rm -f build/compile_driver_bin_stage1
  if ! scripts/memwatch.sh 4096 1 -- mise exec -- just refresh-seed; then
    git checkout "$ORIG_BRANCH"
    echo "ESCALATION: PR#$PR seed regeneration failed (see memwatch output)"; continue
  fi
fi

if ! git diff-index --quiet HEAD -- bootstrap/compile_driver.ll; then
  git add bootstrap/compile_driver.ll
  git commit -m "chore(bootstrap): refresh seed after rebase onto master"
fi

if ! git push --force-with-lease origin "$HEAD_REF" 2>&1 | tail -5; then
  git checkout "$ORIG_BRANCH"
  echo "ESCALATION: PR#$PR force-push-with-lease rejected — remote moved"; continue
fi

git checkout "$ORIG_BRANCH"

# Fetch new head sha, return to Step 2 to requeue.
curl -sf -H "Authorization: token $TOKEN" "$API/pulls/$PR" > "$tmp"
HEAD_SHA=$(jq -r '.head.sha' "$tmp")
echo "PR#$PR: rebased + seed-refreshed, new head=${HEAD_SHA:0:7}, requeueing"
```

After requeue, relaunch the Monitor for any PRs not yet in a done state.

## Bounds

- **Per-PR max rebase attempts: 3.** Beyond that:
  `ESCALATION: PR#$PR — 3 rebase attempts exhausted, master moves too fast`.
- **Poll interval: 60s** (never below 30s).
- **Total wall-clock cap per skill run: 4 hours.**

## Final summary

After all PRs are done, print and clean up:

```sh
echo "DONE: codeberg-merge complete"
echo "  merged:     $MERGED_COUNT"
echo "  skipped:    $SKIPPED_COUNT   (draft/WIP/already-merged)"
echo "  escalated:  $ESCALATED_COUNT"
echo "  PRs:        $PR_ARGS"
rm -f /tmp/cm_pr_*.json /tmp/cm_queue_*.out /tmp/cm_mon_*.json
```

## Hard rules (do not violate)

- Never `git push --force` (always `--force-with-lease`).
- Never pass `--no-verify` to `git commit`.
- Never modify `git config`.
- Never delete a remote branch (`git push -d`, `tea pr close`).
- Never edit any file other than `bootstrap/compile_driver.ll` during
  the rebase (and only via `git checkout --theirs`).
- Never `cd` away from the repo root; all paths are relative to it.
- Never poll CI state in a foreground Bash loop — use Monitor
  (rationale: Bash tool's 10-min cap vs ~40-50min CI wall time).
- Wrap `just refresh-seed` with `scripts/memwatch.sh 4096 1 --` to cap
  RSS at 4 GB (project memory `feedback_memory_watchdog`).
- Skip draft / WIP-titled PRs (project memory
  `feedback_skip_wip_prs_in_automation`).
- If `$ARGUMENTS` is empty AND `tea pr list` returns no PRs, print
  `DONE: no open PRs to handle` and exit 0 — not an escalation.

## Escalation contract

Every escalation line starts with `ESCALATION:` followed by `PR#$PR`
(or `setup` for pre-PR failures), then the reason. Supervising agents
and humans can grep `ESCALATION:` for triage. Halt work on the
escalated PR; continue with the rest of the list.
