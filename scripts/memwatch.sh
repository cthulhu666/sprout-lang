#!/usr/bin/env bash
# Memory watchdog — kills the wrapped command if any of these are exceeded:
#   1. Child RSS (resident pages, summed over descendant tree) > MEMWATCH_RSS_MB (= LIMIT_MB)
#   2. Child VSZ (virtual size, summed over descendant tree)   > MEMWATCH_VSZ_MB (opt-in: unset disables)
#   3. Wall-clock runtime                                      > MEMWATCH_TIMEOUT_SEC (opt-in: unset disables)
#
# System-wide swap/free checks are intentionally absent: macOS keeps "free" near
# zero by design (spare RAM becomes reclaimable file cache), so those signals
# produce constant false positives on machines with reclaimable memory.
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
TIMEOUT_SEC="${MEMWATCH_TIMEOUT_SEC:-}"

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

START_EPOCH=$(date +%s)
"$@" &
CHILD=$!

PEAK_RSS_KB=0
PEAK_VSZ_KB=0
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

  ELAPSED=$(( $(date +%s) - START_EPOCH ))

  if   (( RSS_KB / 1024 > RSS_LIMIT_MB )); then
    KILL_REASON="RSS=$((RSS_KB/1024))MB > ${RSS_LIMIT_MB}MB"
  elif [[ -n "$VSZ_LIMIT_MB" ]] && (( VSZ_KB / 1024 > VSZ_LIMIT_MB )); then
    KILL_REASON="VSZ=$((VSZ_KB/1024))MB > ${VSZ_LIMIT_MB}MB"
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
echo "memwatch: peak_rss=${PEAK_RSS_MB}MB peak_vsz=${PEAK_VSZ_MB}MB wall=${ELAPSED}s" >&2

if [[ -n "$KILL_REASON" ]]; then
  echo "memwatch: killed reason='$KILL_REASON'" >&2
  exit 137
fi
echo "memwatch: ok exit=$EXIT" >&2
exit "$EXIT"
