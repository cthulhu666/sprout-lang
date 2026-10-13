#!/usr/bin/env bash
# Keep-alive A/B: one server source (server.sprout), compiled against the stdlib and runtime from
# before keep-alive and from this tree, then driven by wrk, which reuses connections by default.
#
#   before  every response closes, so wrk opens a TCP connection per request
#   after   the connection stays open between requests
#   after+close  `after` driven with `Connection: close`: it should match `before`, which shows the
#                gain is connection reuse and nothing else
#
# Scenarios: `serve` and `serve_pooled` hit directly at CONNS connections; `serve_pooled` at 4
# connections, which its 8 workers can all keep (up to 6 may hold an idle connection); and, when
# nginx is on PATH, `serve` behind an nginx with an upstream keepalive pool, the setup of #390.
#
# The bench ports' TIME_WAIT sockets are drained before each run: without that the client's
# ephemeral port table, not the server, sets the `before` numbers. The `time_wait` column counts
# those a run left behind — one per connection it opened and closed, the churn keep-alive removes.
#
# Env: BASE (git ref without keep-alive; found automatically), DUR (4s), CONNS (40), ROUNDS (2).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
PORT=8099
NGINX_PORT=8098
DUR="${DUR:-4s}"
CONNS="${CONNS:-40}"
ROUNDS="${ROUNDS:-2}"
COMPILER="$REPO/build/compile_driver_bin_stage1"
CLANG_EXTRA=""
[[ "$(uname)" == "Darwin" ]] && CLANG_EXTRA="-framework Security -framework CoreFoundation"

command -v wrk >/dev/null || { echo "ERROR: wrk not on PATH (brew install wrk)" >&2; exit 1; }
[[ -x "$COMPILER" ]] || { echo "ERROR: $COMPILER not found (run: just bootstrap-from-seed)" >&2; exit 1; }

# The newest commit whose http_server still defaults `connection: close`: HEAD while keep-alive is
# uncommitted, else the parent of the commit that removed the default. Found from history so the
# script keeps working after keep-alive lands.
CLOSE_DEFAULT='with_default_header("connection", "close")'
find_base() {
  local c head_src
  head_src=$(git -C "$REPO" show "HEAD:stdlib/http_server.sprout")
  if [[ "$head_src" == *"$CLOSE_DEFAULT"* ]]; then
    git -C "$REPO" rev-parse HEAD
    return
  fi
  c=$(git -C "$REPO" log -1 --format=%H -G'with_default_header\("connection", "close"\)' -- stdlib/http_server.sprout)
  [[ -n "$c" ]] && git -C "$REPO" rev-parse "$c^"
}
BASE="${BASE:-$(find_base)}" || { echo "ERROR: no pre-keep-alive commit found; set BASE" >&2; exit 1; }

TMP=$(mktemp -d /tmp/sprout_keepalive_XXXXXX)
cleanup() {
  [[ -f "$TMP/nginx/nginx.pid" ]] && kill "$(cat "$TMP/nginx/nginx.pid")" 2>/dev/null
  pkill -f "$TMP/server_" 2>/dev/null
  rm -rf "$TMP"
}
trap cleanup EXIT

build() {  # $1 = stdlib root, $2 = runtime dir, $3 = output binary
  "$COMPILER" --emit-ir "$1" "$DIR/server.sprout" > "$TMP/server.ll" 2>"$TMP/emit.err" \
    || { echo "ERROR: compile failed for $3" >&2; cat "$TMP/emit.err" >&2; exit 1; }
  # shellcheck disable=SC2086
  clang "$TMP/server.ll" "$2"/*.c -O2 $CLANG_EXTRA -o "$3" 2>"$TMP/link.err" \
    || { echo "ERROR: link failed for $3" >&2; cat "$TMP/link.err" >&2; exit 1; }
}

echo "==> before = $(git -C "$REPO" log -1 --format='%h %s' "$BASE")"
mkdir -p "$TMP/base"
git -C "$REPO" archive "$BASE" stdlib runtime | tar -x -C "$TMP/base"
build "$TMP/base/stdlib" "$TMP/base/runtime" "$TMP/server_before"
echo "==> after  = working tree"
build "$REPO/stdlib" "$REPO/runtime" "$TMP/server_after"

# TIME_WAIT sockets on the bench's two ports only: the machine-wide count includes every other
# connection the host makes. Each one is a TCP connection a run opened and closed.
time_wait() {
  if command -v ss >/dev/null; then
    ss -Htan state time-wait "( sport = :$PORT or dport = :$PORT or sport = :$NGINX_PORT or dport = :$NGINX_PORT )" | wc -l | tr -d ' '
  else
    netstat -an -p tcp 2>/dev/null \
      | awk -v a="\\.$PORT\$" -v b="\\.$NGINX_PORT\$" '$6 == "TIME_WAIT" && ($4 ~ a || $5 ~ a || $4 ~ b || $5 ~ b)' | wc -l | tr -d ' '
  fi
}

# TIME_WAIT lasts 2*MSL (30 s on macOS). Waiting for the bench ports to clear keeps the last run's
# sockets out of this run's count and its ephemeral ports free.
drain() {
  for _ in $(seq 1 450); do
    [[ "$(time_wait)" -lt 100 ]] && return 0
    sleep 0.1
  done
}

up() {  # $1 = port
  for _ in $(seq 1 400); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:$1/" && return 0
    sleep 0.01
  done
  return 1
}

start_nginx() {
  mkdir -p "$TMP/nginx/logs"
  cat > "$TMP/nginx/nginx.conf" <<CONF
worker_processes 1;
pid $TMP/nginx/nginx.pid;
error_log $TMP/nginx/logs/error.log warn;
events { worker_connections 1024; }
http {
  access_log off;
  upstream sprout {
    server 127.0.0.1:$PORT;
    keepalive 16;
  }
  server {
    listen 127.0.0.1:$NGINX_PORT;
    location / {
      proxy_pass http://sprout;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
    }
  }
}
CONF
  nginx -p "$TMP/nginx" -c "$TMP/nginx/nginx.conf"
}

# $1 label, $2 binary, $3 MODE, $4 connections, $5 target port, $6.. extra wrk args
run_one() {
  local label="$1" bin="$2" mode="$3" conns="$4" target="$5"; shift 5
  pkill -f "$TMP/server_" 2>/dev/null; sleep 0.1
  drain
  PORT=$PORT MODE=$mode "$bin" >/dev/null 2>"$TMP/server.err" &
  local pid=$!
  up "$PORT" || { echo "$label: SERVER NEVER CAME UP"; cat "$TMP/server.err"; kill $pid 2>/dev/null; return; }
  local out rss tw1
  out=$(wrk -t2 -c"$conns" -d"$DUR" --latency "$@" "http://127.0.0.1:$target/" 2>&1)
  rss=$(ps -o rss= -p $pid 2>/dev/null | awk '{printf "%.0f", $1/1024}')
  tw1=$(time_wait)
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  printf '  %-13s rps=%-10s p50=%-9s p99=%-9s time_wait=%-6s rss=%sMB %s\n' "$label" \
    "$(echo "$out" | awk '/Requests\/sec/{print $2}')" \
    "$(echo "$out" | awk '/^ *50%/{print $2}')" \
    "$(echo "$out" | awk '/^ *99%/{print $2}')" \
    "$tw1" "$rss" \
    "$(echo "$out" | awk '/Socket errors/{print "errors:" $0}' | tr -s ' ')"
}

scenario() {  # $1 title, $2 MODE, $3 connections, $4 target port
  echo "--- $1 (-c$3 -d$DUR)"
  run_one "before" "$TMP/server_before" "$2" "$3" "$4"
  run_one "after" "$TMP/server_after" "$2" "$3" "$4"
  if [[ "$4" == "$PORT" ]]; then
    run_one "after+close" "$TMP/server_after" "$2" "$3" "$4" -H "Connection: close"
  fi
}

for r in $(seq 1 "$ROUNDS"); do
  echo "=== round $r"
  scenario "serve, direct" spawn "$CONNS" "$PORT"
  scenario "serve_pooled, direct" pool "$CONNS" "$PORT"
  scenario "serve_pooled, inside the idle reserve" pool 4 "$PORT"
  if command -v nginx >/dev/null; then
    [[ -f "$TMP/nginx/nginx.pid" ]] || start_nginx
    scenario "serve behind nginx (upstream keepalive 16)" spawn "$CONNS" "$NGINX_PORT"
  fi
done
