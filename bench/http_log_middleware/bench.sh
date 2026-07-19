#!/usr/bin/env bash
# Access-log middleware overhead microbenchmark harness.
#
# Compiles bench/http_log_middleware/http_log_middleware_bench.sprout with the
# current stage-1 compiler and runs it warm, printing self-reported ns/op for the
# access-log middleware (plain vs discarding-sink vs stderr-sink) plus a cost
# breakdown (clock reads, ISO-8601 formatter).
#
# stderr -> /dev/null: the stderr-sink variant writes one line per op, which we
# discard so the loop measures compute + write syscall, not terminal I/O. The
# reported numbers go to stdout via print().
#
# For the end-to-end wrk A/B (which is dominated by connection-close overhead and
# ~25% run-to-run noise, so it cannot resolve the middleware's few microseconds),
# see bench/results-2026-07-19-http-log-middleware.md.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/http_log_middleware_bench.sprout"
BIN="$DIR/http_log_middleware_bench"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: compile failed" >&2; exit 1; }

echo "==> Running (3 warm; first discarded) ..."
for i in 1 2 3 4; do
  echo "--- run $i ---"
  "$BIN" 2>/dev/null
done
