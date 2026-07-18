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

### Step 2 — Proceed to monitoring (do NOT auto-queue the merge)

**Do NOT POST the merge here.** This repo has **no branch protection /
required status checks**, so Codeberg's `merge_when_checks_succeed:true` does
NOT gate — it fast-forwards *immediately*, before CI even starts (confirmed on
PR #102, 2026-06-29: queued HTTP 201, then merged while jobs were still
`status=running`; see memory `project_codeberg_merge_via_tea_api`). Trusting it
silently lands code before CI is green.

Instead, gate the merge on CI yourself: record `HEAD_SHA` and proceed straight
to Step 3. The monitor reports a RELIABLE CI signal (derived from
`actions/tasks` run statuses, not the ghost commit-status), and Step 3 performs
the fast-forward merge **explicitly, only once that signal is `success`**. This
is correct even if branch protection is later enabled — the explicit merge
fires after green, by which point any required checks are satisfied too.

### Step 3 — Wait for a terminal state via Monitor

**Do not poll in a foreground bash loop** — a foreground call blocks the
whole turn, shell state does not persist across invocations, and even a
normal ~5-10 min run (Kuba-confirmed 2026-07-10) can reach or exceed the
Bash tool's 10-min per-call cap. Use the `Monitor` tool with
`persistent: true` and let each state change arrive as a conversation
event you react to in normal turn cycles.

The polling logic lives in `scripts/codeberg/pr-monitor.sh`. Launch it
through the Monitor tool:

```
Monitor:
  description: "PR #<N>[, #<M>...] — wait for terminal state"
  persistent: true
  timeout_ms: 3600000
  command: scripts/codeberg/pr-monitor.sh <N> [<M> ...]
```

The script emits one line per state change in the form
`PR#<N>: state=<X> merged=<Y> ci=<Z> sha=<S>`, polls every 60s, and
exits when every PR has reached a terminal state. Source is the
authoritative reference for the polling shape; don't reinvent it
inline.

For each event line you receive, decide:

- `merged=true` → `PR#$PR: merged ✓`, mark done.
- `state=closed` && `merged=false` → `ESCALATION: PR#$PR closed without merge`, mark done.
- `ci=failure` → `ESCALATION: PR#$PR CI failed at $SHA`, mark done.
- `ci=success` && `state=open` && `merged=false` → **candidate to merge — but
  RE-VERIFY GROUND TRUTH FIRST.** `TaskStop` the monitor, then re-check the
  combined commit status and only merge if it is truly `success`. Do NOT trust
  the monitor's `ci=success` alone: it is a heuristic over Actions runs and can
  fire early in the `setup`-green / `test`-not-spawned window (this merged PR#121
  prematurely, 2026-07-03; see `feedback_codeberg_merge_reverify_ci`).
  ```sh
  # _lib.sh provides ci_is_green; if merging inline (not via a sourced lib),
  # inline the check: state=$(curl -sf -H "Authorization: token $TOKEN" \
  #   "$API/commits/$SHA/status" | jq -r '.state'); [ "$state" = success ]
  if ! ci_is_green "$SHA"; then
    echo "PR#$PR: monitor said green but commit status != success — keep waiting"
    # relaunch the monitor; do NOT merge.
  else
  # $SHA must be the FULL 40-char head SHA (pr-monitor emits it full via
  # format_monitor_line). A truncated SHA is rejected HTTP 409 "head out of
  # date" even when genuinely fast-forwardable — indistinguishable from the
  # real master-moved race, so it triggers a needless rebase + seed regen
  # (project_codeberg_ffmerge_needs_full_sha).
  HTTP=$(curl -s -o /tmp/cm_merge_$PR.out -w "%{http_code}" \
    -X POST "$API/pulls/$PR/merge" \
    -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
    -d "{\"Do\":\"fast-forward-only\",\"head_commit_id\":\"$SHA\"}")
  fi
  ```
  - `HTTP=200`/`201` → `PR#$PR: merged ✓`, mark done.
  - `HTTP=405`/`409` (not fast-forwardable — master moved during CI) → run
    Step 4 (rebase), then relaunch the monitor for any PRs not yet done.
  - any other code → `ESCALATION: PR#$PR ff-merge failed HTTP=$HTTP body=<see /tmp/cm_merge_$PR.out>`.
- Otherwise (`ci=pending`, `ci=no-status`) → no action; the monitor
  continues.

Wall-clock cap: 4 hours total. Beyond that, `TaskStop` the monitor and
escalate any still-open PRs with
`ESCALATION: PR#$PR — 4-hour cap reached`.

### Step 4 — Rebase + regenerate seed (then re-monitor)

Autonomous-handling path for the master-moved race. The full Step 4
algorithm lives in `scripts/codeberg/pr-rebase.sh`:

```sh
scripts/codeberg/pr-rebase.sh <PR-number>
```

The script handles, in order:
1. Snapshot the PR + WIP/draft/merged/closed gates (re-checks Step 1's gates so the script is callable standalone).
2. Working-tree clean check (refuses to start if dirty).
3. Worktree-collision detection — if `$HEAD_REF` is checked out elsewhere via `git worktree`, work on a temp local branch and force-push via explicit refspec (`<temp>:<canonical>`).
4. `git fetch origin master <head-ref>` then `git checkout -B <local> origin/<head-ref>`.
5. `git rebase origin/master`, with a loop that takes `--theirs` on `bootstrap/compile_driver.ll` conflicts and escalates on any other conflict (or on stuck-with-no-conflict).
6. **Bug-#3 heuristic**: `git diff --name-only origin/master HEAD -- stdlib/compiler/ stdlib/prelude.sprout`. If empty → skip seed regen (saves 30-60 min on tooling-only PRs). Otherwise → `scripts/memwatch.sh 4096 1 -- mise exec -- just refresh-seed`.
7. `just fmt` if compiler source was touched.
8. Commit the regenerated seed iff it actually differs from HEAD.
9. `git push --force-with-lease origin <push-refspec>`.
10. Restore the original working branch.
11. Report the new head SHA. It does NOT requeue an auto-merge (that would
    fast-forward the rebased head before its CI runs — the bug this skill
    avoids); the caller re-monitors and merges explicitly once CI is green.

The script's exit code tells you everything:
- **0** → success. PR is force-pushed. Relaunch the monitor on the new head;
  CI re-runs, and Step 3 performs the explicit ff-merge once it goes green.
- **1** → escalation. The script prints `ESCALATION: PR#<N> <reason>` to stdout before exiting. Stop work on this PR; surface to the human/supervisor.
- **2** → usage error (wrong arg count).

Run the script via Bash with `run_in_background: true` if the seed regen
path is going to be hit (it can take 30-60 min for compiler-source PRs).
For tooling-only PRs, the run completes in well under a minute and can
be run foreground.

After a successful `pr-rebase.sh`, relaunch the Monitor on any PRs not
yet in a done state — CI re-runs on the new head, and Step 3's
`ci=success` handler performs the explicit ff-merge once it is green.

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
  (rationale: foreground bash blocks the turn + no state persistence across
  calls; the 10-min per-call cap can't reliably outlast even a normal
  ~5-10 min CI run).
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
