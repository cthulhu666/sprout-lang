#!/usr/bin/env bash
# Worker-pool vs task-per-connection HTTP server A/B (docs/green-task-pool-v0.md §5.3).
#
# Both servers handle requests with BYTE-IDENTICAL code (read_avail -> fixed 200 -> close);
# the only difference is the concurrency model, so the measurement isolates that variable.
#
# TIME_WAIT drain barrier: `Connection: close` means one TCP connection per request, so a
# few 4s runs at ~30k req/s leave >10k sockets in TIME_WAIT. Without draining between runs
# the port table -- not Sprout -- dominates, and the baseline reads ~2x low. This barrier
# is load-bearing; do not remove it to "speed up" the benchmark.
#
# READ p99 WITH CARE -- at these rates it measures the CLIENT, not the server. There are
# only 16384 ephemeral ports (net.inet.ip.portrange 49152-65535), and a 4s run at ~32k req/s
# opens ~131k connections: 8x the range, so the client stalls on TIME_WAIT recycling and p99
# blows out to tens of ms while p50 stays in the hundreds of us. Drained, and kept inside the
# port range (`DUR=1s CONNS=2`), the same pooled server measures p50=35us / p99=243us. So a
# long run's p99 is not a server property. Both a small backlog and GC were tested as causes
# and both ruled out -- see docs/green-task-pool-v0.md §5.3. Throughput and p50/p90 are the
# figures to compare here; fixing the tail properly needs server-side keep-alive.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
PORT=8099
DUR="${DUR:-4s}"
CONNS="${CONNS:-40}"
ROUNDS="${ROUNDS:-3}"

command -v wrk >/dev/null || { echo "ERROR: wrk not on PATH (brew install wrk)" >&2; exit 1; }
[[ -x "$REPO/build/compile_driver_bin_stage1" ]] || {
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2; exit 1; }

for v in spawn_server pool_server; do
  echo "==> Compiling $v ..."
  (cd "$REPO" && mise exec -- just compile-native "$DIR/$v.sprout" "$DIR/$v") >/dev/null 2>&1 \
    || { echo "ERROR: compile failed for $v" >&2; exit 1; }
done

drain() {
  for _ in $(seq 1 400); do
    [[ "$(netstat -an -p tcp 2>/dev/null | grep -c TIME_WAIT)" -lt 1500 ]] && return 0
  done
}

run_one() {
  local name="$1" bin="$2"
  for v in spawn_server pool_server; do pkill -f "$DIR/$v" >/dev/null 2>&1; done
  for _ in $(seq 1 200); do curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/health" || break; done
  drain
  "$bin" >/dev/null 2>&1 &
  local pid=$! up=0
  for _ in $(seq 1 400); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/health" && { up=1; break; }
  done
  if [[ $up -eq 0 ]]; then echo "$name: SERVER NEVER CAME UP"; kill $pid 2>/dev/null; return; fi
  local out rss
  out=$(wrk -t2 -c"$CONNS" -d"$DUR" --latency "http://127.0.0.1:$PORT/health" 2>&1)
  rss=$(ps -o rss= -p $pid 2>/dev/null | awk '{printf "%.0f", $1/1024}')
  printf '%-14s rps=%-11s p50=%-9s p90=%-9s p99=%-9s rss=%sMB\n' "$name" \
    "$(echo "$out" | awk '/Requests\/sec/{print $2}')" \
    "$(echo "$out" | awk '/^ *50%/{print $2}')" \
    "$(echo "$out" | awk '/^ *90%/{print $2}')" \
    "$(echo "$out" | awk '/^ *99%/{print $2}')" "$rss"
  kill $pid >/dev/null 2>&1; wait $pid 2>/dev/null
}

echo "==> Interleaved A/B, $ROUNDS rounds, -c$CONNS -d$DUR"
echo "    (compare rps/p50/p90; p99 at this rate measures client port exhaustion -- see doc 5.3)"
for r in $(seq 1 "$ROUNDS"); do
  echo "--- round $r ---"
  run_one "spawn" "$DIR/spawn_server"
  run_one "pool"  "$DIR/pool_server"
done
