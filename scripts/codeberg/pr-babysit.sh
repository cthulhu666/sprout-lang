#!/usr/bin/env bash
# End-to-end merge-queue orchestrator: drive one or more open PRs all the way to
# a fast-forward merge in a SINGLE invocation, deterministically — replacing the
# hand-driven event loop that used to live in the codeberg-merge SKILL.md prose.
#
#   scripts/codeberg/pr-babysit.sh [--dry-run] [--interval=N] [--max-wall=N] <pr> [<pr> ...]
#
# What it does, per cycle, for each still-active PR:
#   1. Snapshot + gate: skip draft / WIP-titled; mark merged; escalate a PR
#      closed-without-merge.
#   2. CI gate: merge ONLY when the authoritative combined commit status is
#      `success` (ci_is_green) — not the Actions-run heuristic. A `failure`
#      signal escalates; anything not-yet-green just waits.
#   3. Merge: POST a fast-forward-only merge with the FULL head SHA. On success,
#      stop scanning this cycle and re-evaluate the rest against the moved master
#      (serialized merges — the ff-only cascade resolves in order, not by thrash).
#   4. Non-success is NOT trusted by status code (Gitea returns 500, not 405/409,
#      for a non-ff ff-only merge). Diagnose with `git merge-base --is-ancestor`:
#        - head already in master  -> recheck next cycle (merged elsewhere)
#        - head descends from master (genuinely ff-able) -> transient; retry
#        - diverged -> pr-rebase.sh, then let CI re-run and re-gate
#   5. Caps: <=3 rebases and <=3 transient-retries per PR, and a wall-clock cap;
#      breaching any escalates that PR but does NOT stop the others.
#
# Output is line-oriented for a supervising agent: `MERGED:` / `ESCALATION:` /
# `DONE:` are the terminal signals; `[babysit]` lines are progress. Exit 0 iff
# every PR ended merged-or-skipped (none escalated, none still pending).
#
# NOTE ON AUTHORISATION: this script merges every PR you pass it, INCLUDING PRs
# the agent authored — invoking it is the reviewed-and-authorised signal (Kuba:
# "babysit PRs X-Z" == "I reviewed them, merge them"). Launch it in the
# background; it self-terminates at the wall-clock cap.

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

INTERVAL=${CM_INTERVAL:-60}
MAX_WALL=${CM_MAX_WALL:-14400}     # 4h, matching the skill's cap
MAX_REBASE=${CM_MAX_REBASE:-3}
MAX_RETRY=${CM_MAX_MERGE_RETRY:-3}
DRY=0
PRS=()
for a in "$@"; do
  case "$a" in
    --dry-run)      DRY=1 ;;
    --interval=*)   INTERVAL="${a#*=}" ;;
    --max-wall=*)   MAX_WALL="${a#*=}" ;;
    [0-9]*)         PRS+=("$a") ;;
    *) echo "usage: pr-babysit.sh [--dry-run] [--interval=N] [--max-wall=N] <pr> [<pr> ...]" >&2; exit 2 ;;
  esac
done
if [ "${#PRS[@]}" -lt 1 ]; then
  echo "usage: pr-babysit.sh [--dry-run] [--interval=N] [--max-wall=N] <pr> [<pr> ...]" >&2
  exit 2
fi

declare -A st reb ret
for pr in "${PRS[@]}"; do st[$pr]=active; reb[$pr]=0; ret[$pr]=0; done
merged_n=0 skip_n=0 esc_n=0

log() { echo "[babysit] $*"; }
active_left() { local pr; for pr in "${PRS[@]}"; do [ "${st[$pr]}" = active ] && return 0; done; return 1; }

log "babysitting PRs: ${PRS[*]} (interval=${INTERVAL}s cap=${MAX_WALL}s dry=${DRY})"
start=$SECONDS
while active_left; do
  if [ $((SECONDS - start)) -ge "$MAX_WALL" ]; then
    for pr in "${PRS[@]}"; do
      if [ "${st[$pr]}" = active ]; then
        echo "ESCALATION: PR#$pr — wall-clock cap (${MAX_WALL}s) reached"; st[$pr]=escalated; esc_n=$((esc_n+1))
      fi
    done
    break
  fi

  just_merged=0
  for pr in "${PRS[@]}"; do
    [ "${st[$pr]}" = active ] || continue

    eval "$(pr_snapshot "$pr" P_)"
    if [ "${P_FETCH_OK:-0}" != "1" ]; then log "PR#$pr fetch failed (transient) — retry next cycle"; continue; fi
    if [ "${P_MERGED:-false}" = "true" ]; then echo "MERGED: PR#$pr (${P_TITLE:-})"; st[$pr]=merged; merged_n=$((merged_n+1)); continue; fi
    if [ "${P_DRAFT:-false}" = "true" ]; then log "SKIP PR#$pr: draft"; st[$pr]=skipped; skip_n=$((skip_n+1)); continue; fi
    if title_is_wip "${P_TITLE:-}"; then log "SKIP PR#$pr: WIP-titled"; st[$pr]=skipped; skip_n=$((skip_n+1)); continue; fi
    if [ "${P_STATE:-?}" = "closed" ]; then echo "ESCALATION: PR#$pr closed without merging"; st[$pr]=escalated; esc_n=$((esc_n+1)); continue; fi

    # ---- CI gate: ground truth is the combined commit status.
    if ! ci_is_green "${P_HEAD_SHA}"; then
      ci=$(ci_from_tasks "$(codeberg_curl GET "/actions/tasks?limit=50")" "${P_HEAD_SHA}")
      if [ "$ci" = "failure" ]; then
        echo "ESCALATION: PR#$pr CI failed at ${P_HEAD_SHA}"; st[$pr]=escalated; esc_n=$((esc_n+1)); continue
      fi
      log "PR#$pr CI ${ci} @ ${P_HEAD_SHA:0:10} — waiting"; continue
    fi

    if [ "$DRY" = "1" ]; then
      log "[dry-run] PR#$pr CI green — would ff-merge @ ${P_HEAD_SHA:0:10}"
      st[$pr]=merged; merged_n=$((merged_n+1)); continue
    fi

    # ---- CI green: attempt the fast-forward-only merge (full SHA required).
    HTTP=$(codeberg_curl POST "/pulls/$pr/merge" -o "/tmp/cm_bmerge_$pr.out" -w "%{http_code}" \
             -H "Content-Type: application/json" \
             -d "{\"Do\":\"fast-forward-only\",\"head_commit_id\":\"${P_HEAD_SHA}\"}")
    if is_merge_success "$HTTP"; then
      echo "MERGED: PR#$pr (${P_TITLE:-})"; st[$pr]=merged; merged_n=$((merged_n+1)); just_merged=1
      break   # master moved — restart so the rest re-evaluate against the new tip
    fi

    # ---- non-success: diagnose fast-forwardability rather than trust the HTTP code.
    git fetch origin master "${P_HEAD_REF}" >/dev/null 2>&1 || true
    if git merge-base --is-ancestor "${P_HEAD_SHA}" origin/master 2>/dev/null; then
      log "PR#$pr merge HTTP=$HTTP but head already in master — rechecking next cycle"; continue
    elif git merge-base --is-ancestor origin/master "${P_HEAD_SHA}" 2>/dev/null; then
      ret[$pr]=$(( ${ret[$pr]} + 1 ))
      if [ "${ret[$pr]}" -gt "$MAX_RETRY" ]; then
        echo "ESCALATION: PR#$pr fast-forwardable but merge kept failing HTTP=$HTTP (body: $(head -c 200 "/tmp/cm_bmerge_$pr.out" 2>/dev/null))"
        st[$pr]=escalated; esc_n=$((esc_n+1)); continue
      fi
      log "PR#$pr ff-able but merge HTTP=$HTTP — transient, retry ${ret[$pr]}/${MAX_RETRY} next cycle"; continue
    else
      reb[$pr]=$(( ${reb[$pr]} + 1 ))
      if [ "${reb[$pr]}" -gt "$MAX_REBASE" ]; then
        echo "ESCALATION: PR#$pr — ${MAX_REBASE} rebase attempts exhausted, master moves too fast"
        st[$pr]=escalated; esc_n=$((esc_n+1)); continue
      fi
      log "PR#$pr not fast-forwardable (HTTP $HTTP) — rebasing (attempt ${reb[$pr]}/${MAX_REBASE})"
      if ! "$SCRIPT_DIR/pr-rebase.sh" "$pr"; then
        echo "ESCALATION: PR#$pr rebase failed (see pr-rebase.sh output above)"
        st[$pr]=escalated; esc_n=$((esc_n+1)); continue
      fi
      # New head force-pushed; CI re-runs; next cycles re-gate. (Rebases don't move master, so no break.)
    fi
  done

  active_left || break
  [ "$just_merged" = "1" ] && continue   # a merge moved master — re-evaluate immediately, skip the sleep
  sleep "$INTERVAL"
done

pend_n=0
for pr in "${PRS[@]}"; do [ "${st[$pr]}" = active ] && pend_n=$((pend_n+1)); done
echo "DONE: babysit complete — merged=$merged_n skipped=$skip_n escalated=$esc_n pending=$pend_n  (PRs: ${PRS[*]})"
[ "$esc_n" -eq 0 ] && [ "$pend_n" -eq 0 ]
