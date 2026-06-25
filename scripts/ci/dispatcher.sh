#!/usr/bin/env bash
# Sprout CI dispatcher — one tick of the start/stop control loop.
#
# Runs on the always-on dispatcher VM, invoked every ~45s by
# sprout-ci-dispatcher.timer. Each tick:
#   1. Asks Codeberg's Actions API how many workflow runs are NON-TERMINAL
#      (queued / waiting / running / blocked) for the repo.
#   2. If there is work and the e2-standard-4 worker is TERMINATED -> start it.
#   3. If there is no work for IDLE_GRACE_TICKS consecutive ticks and the
#      worker is RUNNING -> stop it.
#
# Why the `actions/runs` endpoint and not `actions/tasks`:
#   A *task* is a runner-assigned work unit, so `actions/tasks` is blind to
#   jobs still WAITING for a runner (go-gitea/gitea#35134) — the exact state we
#   must detect to decide whether to boot. A *run* is created by the trigger
#   event itself, before any runner exists, so its status reflects queued work.
#
# Failure direction is intentional: any error aborts the tick (set -e) and the
# worker is left in whatever state it was. We never create or delete instances,
# only start/stop — a stuck dispatcher costs $0, never a runaway.
set -euo pipefail

# --- Config (sourced from the env file by the systemd unit) ----------------
: "${CODEBERG_OWNER:?set CODEBERG_OWNER}"
: "${CODEBERG_REPO:?set CODEBERG_REPO}"
: "${CODEBERG_TOKEN:?set CODEBERG_TOKEN}"          # read-scoped Codeberg token
: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${GCP_ZONE:?set GCP_ZONE}"
: "${WORKER_INSTANCE:?set WORKER_INSTANCE}"
IDLE_GRACE_TICKS="${IDLE_GRACE_TICKS:-3}"          # idle ticks before stop (~2 min at 45s)
MAX_UPTIME_MIN="${MAX_UPTIME_MIN:-0}"              # 0 = disabled; >0 = force-stop safety cap
STATE_DIR="${STATE_DIR:-/var/lib/sprout-ci-dispatcher}"
API_BASE="${API_BASE:-https://codeberg.org/api/v1}"

IDLE_FILE="$STATE_DIR/idle_ticks"
STARTED_FILE="$STATE_DIR/started_epoch"
mkdir -p "$STATE_DIR"

log() { printf '%s dispatcher: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# --- 1. Count non-terminal workflow runs -----------------------------------
# Terminal statuses are a denylist: anything NOT in this set counts as "work
# present", so an unknown/new status fails safe toward keeping the worker up.
runs_url="$API_BASE/repos/$CODEBERG_OWNER/$CODEBERG_REPO/actions/runs?limit=50"
runs_json="$(curl -fsS --max-time 20 -H "Authorization: token $CODEBERG_TOKEN" "$runs_url")"

# Forgejo may serialize `status` as a string ("success") or its DB enum int
# (1=success,2=failure,3=cancelled,4=skipped). Treat BOTH encodings as terminal
# so a representation surprise can't silently match nothing (which would leave
# the worker running forever). Everything else (waiting/running/blocked/unknown)
# counts as active.
active="$(printf '%s' "$runs_json" | jq '
  [ (.workflow_runs // [])[]
    | (.status) as $s
    | ( (($s | type) == "string" and (["success","failure","cancelled","skipped"] | index($s)) != null)
        or (($s | type) == "number" and ([1,2,3,4] | index($s)) != null) ) as $terminal
    | select($terminal | not) ]
  | length')"

# --- 2. Worker power state --------------------------------------------------
state="$(gcloud compute instances describe "$WORKER_INSTANCE" \
  --project "$GCP_PROJECT" --zone "$GCP_ZONE" \
  --format='value(status)' 2>/dev/null || echo UNKNOWN)"

log "active_runs=$active worker=$state idle_ticks=$(cat "$IDLE_FILE" 2>/dev/null || echo 0)"

start_worker() {
  log "starting worker (active_runs=$active)"
  gcloud compute instances start "$WORKER_INSTANCE" \
    --project "$GCP_PROJECT" --zone "$GCP_ZONE"
  date +%s > "$STARTED_FILE"
  : > "$IDLE_FILE"
}

stop_worker() {
  log "stopping worker ($1)"
  gcloud compute instances stop "$WORKER_INSTANCE" \
    --project "$GCP_PROJECT" --zone "$GCP_ZONE"
  : > "$IDLE_FILE"
  rm -f "$STARTED_FILE"
}

# --- 3. Decide -------------------------------------------------------------
if [ "$active" -gt 0 ]; then
  : > "$IDLE_FILE"                                 # reset idle counter
  [ "$state" = "TERMINATED" ] && start_worker
  exit 0
fi

# No active work.
if [ "$state" = "RUNNING" ]; then
  ticks="$(cat "$IDLE_FILE" 2>/dev/null || echo 0)"
  ticks=$((ticks + 1))
  echo "$ticks" > "$IDLE_FILE"
  if [ "$ticks" -ge "$IDLE_GRACE_TICKS" ]; then
    stop_worker "idle for $ticks ticks"
  fi
fi

# --- 4. Safety cap: force-stop a wedged worker (opt-in) --------------------
if [ "$MAX_UPTIME_MIN" -gt 0 ] && [ "$state" = "RUNNING" ] && [ -f "$STARTED_FILE" ]; then
  up_min=$(( ( $(date +%s) - $(cat "$STARTED_FILE") ) / 60 ))
  if [ "$up_min" -ge "$MAX_UPTIME_MIN" ]; then
    log "WARNING: worker up ${up_min}m >= cap ${MAX_UPTIME_MIN}m — force-stopping (may kill a long job)"
    stop_worker "max-uptime cap ${MAX_UPTIME_MIN}m"
  fi
fi
