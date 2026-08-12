#!/usr/bin/env bash
# scripts/http_client_binary_gate.sh
#
# Regression gate for the HTTP CLIENT's response body (code review finding 8): a body containing
# 0x00 or non-UTF-8 bytes must arrive byte-for-byte, not truncated at the first NUL. Both body paths
# are covered, because the client has two and they failed differently:
#   /binary   Content-Length      -> the body was silently truncated to 4 bytes and returned Ok
#   /chunked  Transfer-Encoding   -> http_decode_chunked_body measured chunk data with strlen and
#                                    reported "truncated chunk data", failing a valid response
#
# TWO PROCESSES, one binary; tests/http_client/binary_body.spr plays both roles by argv. This was
# once forced — `http_request` blocked the OS thread, so a Sprout server task and a Sprout http_get
# in the same process deadlocked. The client parks now, so the split is no longer required; it is
# retained because it drives the client from a peer with its own scheduler, making the multi-read
# delivery of a body a matter of real socket timing rather than cooperative scheduling.
#
# The peer is deliberately NOT a python3 helper: python3 is absent from the Linux smoke container,
# so `just linux-run http-client-binary-gate` could not run the gate at all. Using the fixture
# itself keeps the gate dependency-free everywhere the compiler already works.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPILER="${SPROUT_STAGE1:-$REPO_ROOT/build/compile_driver_bin_stage1}"
PORT="${SPROUT_HTTP_GATE_PORT:-28771}"
FIXTURE="$REPO_ROOT/tests/http_client/binary_body.spr"

if [[ ! -x "$COMPILER" ]]; then
  echo "ERROR: compiler not found at $COMPILER" >&2
  exit 1
fi

TMPD="$(mktemp -d /tmp/sprout_http_client_gate_XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$TMPD"
}
trap cleanup EXIT

"$COMPILER" --emit-ir "$REPO_ROOT/stdlib" --package-root "$REPO_ROOT" "$FIXTURE" > "$TMPD/fixture.ll" 2>"$TMPD/emit.err" || {
  echo "ERROR: emit-IR failed for $FIXTURE" >&2; cat "$TMPD/emit.err" >&2; exit 1; }

CLANG_EXTRA=()
if [[ "$(uname -s)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
fi
clang "$TMPD/fixture.ll" "$REPO_ROOT"/runtime/*.c "${CLANG_EXTRA[@]}" -o "$TMPD/fixture" 2>"$TMPD/link.err" || {
  echo "ERROR: link failed for $FIXTURE" >&2; tail -20 "$TMPD/link.err" >&2; exit 1; }

"$TMPD/fixture" serve "$PORT" > "$TMPD/server.out" 2>"$TMPD/server.err" &
SERVER_PID=$!

# Wait for the bound listener rather than sleeping a fixed amount.
for _ in $(seq 1 100); do
  if grep -q ready "$TMPD/server.out" 2>/dev/null; then break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
  sleep 0.1
done
if ! grep -q ready "$TMPD/server.out" 2>/dev/null; then
  echo "ERROR: fixture server did not start" >&2
  cat "$TMPD/server.err" >&2
  exit 1
fi

"$TMPD/fixture" "$PORT" > "$TMPD/run.out" 2>"$TMPD/run.err" || {
  echo "ERROR: client exited non-zero" >&2; cat "$TMPD/run.out" "$TMPD/run.err" >&2; exit 1; }

status=0
for label in content-length chunked; do
  got="$(grep "^$label: " "$TMPD/run.out" | head -1 | sed "s/^$label: //")"
  if [[ "$got" != "20" ]]; then
    echo "FAIL [$label]: body was '$got', expected 20 (truncated at the first NUL?)" >&2
    status=1
  else
    echo "PASS [$label]: 20 bytes delivered intact"
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "--- client output ---" >&2; cat "$TMPD/run.out" >&2
  echo "--- server stderr ---" >&2; cat "$TMPD/server.err" >&2
  exit 1
fi

echo "==> http-client-binary gate: OK"
