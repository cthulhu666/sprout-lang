#!/usr/bin/env bash
# Memory watchdog with multi-signal tripwires (macOS-friendly).
#
# Kills the wrapped command if ANY of these are exceeded:
#   1. Child RSS (resident pages, summed over descendant tree)        > MEMWATCH_RSS_MB    (= LIMIT_MB)
#   2. Child VSZ (virtual size, summed over descendant tree)          > MEMWATCH_VSZ_MB    (opt-in: unset disables)
#   3. System swap usage rose                                         > baseline + MEMWATCH_SWAP_DELTA_PCT (= 30)
#      OR system swap usage exceeded a hard ceiling                   > MEMWATCH_SWAP_HARD_PCT (= 95)
#   4. System free memory dropped                                     > baseline - MEMWATCH_FREE_DROP_MB (= 1024)
#      OR system free memory fell below a hard floor                  < MEMWATCH_FREE_HARD_MB (= 100)
#   5. Wall-clock runtime                                             > MEMWATCH_TIMEOUT_SEC (unset = no cap)
#
# Why these defaults: on macOS, VSZ is mostly noise (a trivial bash reports >100 GB
# of mapped guard pages and shared regions). RSS catches genuine resident growth.
# System swap/free pressure detects the case where the wrapped process is *causing*
# host-wide thrashing even though its own RSS looks small (which is what killed Claude
# previously). Delta-from-baseline avoids false trips when the host starts already
# under pressure; the hard floors are a final backstop so a runaway process can't
# squeeze the host further into the danger zone.
#
# Usage: scripts/memwatch.sh <limit_mb> <poll_sec> -- <cmd> [args...]
# Exit:  child's exit code, or 137 if the watchdog killed it.
set -uo pipefail

LIMIT_MB="${1:-4096}"
POLL="${2:-1}"
shift 2
[[ "${1:-}" == "--" ]] && shift

RSS_LIMIT_MB="${MEMWATCH_RSS_MB:-$LIMIT_MB}"
VSZ_LIMIT_MB="${MEMWATCH_VSZ_MB:-}"
SWAP_DELTA_PCT="${MEMWATCH_SWAP_DELTA_PCT:-30}"
SWAP_HARD_PCT="${MEMWATCH_SWAP_HARD_PCT:-95}"
FREE_DROP_MB="${MEMWATCH_FREE_DROP_MB:-1024}"
FREE_HARD_MB="${MEMWATCH_FREE_HARD_MB:-100}"
TIMEOUT_SEC="${MEMWATCH_TIMEOUT_SEC:-}"

PAGE_SIZE=$(sysctl -n hw.pagesize 2>/dev/null || echo 4096)

descendants() {
  local roots=("$@") all=("$@") next=()
  while (( ${#roots[@]} > 0 )); do
    next=()
    for p in "${roots[@]}"; do
      local kids; kids=$(pgrep -P "$p" 2>/dev/null || true)
      for k in $kids; do next+=("$k"); all+=("$k"); done
    done
    roots=("${next[@]}")
  done
  printf '%s\n' "${all[@]}"
}

# System swap usage as integer percent (0 if no swap configured).
swap_pct() {
  local line total used
  line=$(sysctl -n vm.swapusage 2>/dev/null || echo "")
  total=$(printf '%s\n' "$line" | sed -n 's/.*total = \([0-9.]*\)M.*/\1/p')
  used=$(printf '%s\n' "$line"  | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p')
  awk -v u="${used:-0}" -v t="${total:-0}" 'BEGIN { if (t > 0) printf "%d", (u * 100) / t; else print 0 }'
}

# System free memory in MB (free + speculative pages, both reclaimable).
free_mb() {
  vm_stat 2>/dev/null | awk -v ps="$PAGE_SIZE" '
    /Pages free/        { gsub(/[^0-9]/, "", $NF); free=$NF }
    /Pages speculative/ { gsub(/[^0-9]/, "", $NF); spec=$NF }
    END { printf "%d", ((free + spec) * ps) / 1048576 }
  '
}

# Snapshot baseline BEFORE launching the child so the child's own footprint is
# attributed to it.
BASELINE_SWAP_PCT=$(swap_pct)
BASELINE_FREE_MB=$(free_mb)

START_EPOCH=$(date +%s)
"$@" &
CHILD=$!

PEAK_RSS_KB=0
PEAK_VSZ_KB=0
PEAK_SWAP_PCT=$BASELINE_SWAP_PCT
MIN_FREE_MB=$BASELINE_FREE_MB
KILL_REASON=""

kill_tree() {
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null
  done < <(descendants "$CHILD")
}

while kill -0 "$CHILD" 2>/dev/null; do
  RSS_KB=0; VSZ_KB=0
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    read -r r v < <(ps -o rss=,vsz= -p "$pid" 2>/dev/null)
    [[ -n "${r:-}" ]] && RSS_KB=$((RSS_KB + r))
    [[ -n "${v:-}" ]] && VSZ_KB=$((VSZ_KB + v))
  done < <(descendants "$CHILD")
  (( RSS_KB > PEAK_RSS_KB )) && PEAK_RSS_KB=$RSS_KB
  (( VSZ_KB > PEAK_VSZ_KB )) && PEAK_VSZ_KB=$VSZ_KB

  SWAP_PCT=$(swap_pct)
  (( SWAP_PCT > PEAK_SWAP_PCT )) && PEAK_SWAP_PCT=$SWAP_PCT

  FREE_MB=$(free_mb)
  (( FREE_MB < MIN_FREE_MB )) && MIN_FREE_MB=$FREE_MB

  ELAPSED=$(( $(date +%s) - START_EPOCH ))

  if   (( RSS_KB / 1024 > RSS_LIMIT_MB )); then
    KILL_REASON="RSS=$((RSS_KB/1024))MB > ${RSS_LIMIT_MB}MB"
  elif [[ -n "$VSZ_LIMIT_MB" ]] && (( VSZ_KB / 1024 > VSZ_LIMIT_MB )); then
    KILL_REASON="VSZ=$((VSZ_KB/1024))MB > ${VSZ_LIMIT_MB}MB"
  elif (( SWAP_PCT > BASELINE_SWAP_PCT + SWAP_DELTA_PCT )); then
    KILL_REASON="swap rose ${BASELINE_SWAP_PCT}%→${SWAP_PCT}% (Δ>${SWAP_DELTA_PCT})"
  elif (( SWAP_PCT > SWAP_HARD_PCT )); then
    KILL_REASON="swap=${SWAP_PCT}% > hard ${SWAP_HARD_PCT}%"
  elif (( FREE_MB < BASELINE_FREE_MB - FREE_DROP_MB )); then
    KILL_REASON="free dropped ${BASELINE_FREE_MB}MB→${FREE_MB}MB (Δ>${FREE_DROP_MB}MB)"
  elif (( FREE_MB < FREE_HARD_MB )); then
    KILL_REASON="free=${FREE_MB}MB < hard floor ${FREE_HARD_MB}MB"
  elif [[ -n "$TIMEOUT_SEC" ]] && (( ELAPSED > TIMEOUT_SEC )); then
    KILL_REASON="wall=${ELAPSED}s > ${TIMEOUT_SEC}s"
  fi

  if [[ -n "$KILL_REASON" ]]; then
    echo "memwatch: TRIPPED ($KILL_REASON); killing PID $CHILD and descendants" >&2
    kill_tree
    break
  fi

  sleep "$POLL"
done

wait "$CHILD" 2>/dev/null
EXIT=$?

PEAK_RSS_MB=$((PEAK_RSS_KB / 1024))
PEAK_VSZ_MB=$((PEAK_VSZ_KB / 1024))
ELAPSED=$(( $(date +%s) - START_EPOCH ))
echo "memwatch: peak_rss=${PEAK_RSS_MB}MB peak_vsz=${PEAK_VSZ_MB}MB swap=${BASELINE_SWAP_PCT}%→${PEAK_SWAP_PCT}% free=${BASELINE_FREE_MB}MB→${MIN_FREE_MB}MB wall=${ELAPSED}s" >&2

if [[ -n "$KILL_REASON" ]]; then
  echo "memwatch: killed reason='$KILL_REASON'" >&2
  exit 137
fi
echo "memwatch: ok exit=$EXIT" >&2
exit "$EXIT"
