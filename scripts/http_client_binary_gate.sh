#!/usr/bin/env bash
# scripts/http_client_binary_gate.sh
#
# Regression gate for the HTTP CLIENT's response body (code review finding 8): a body containing
# 0x00 or non-UTF-8 bytes must arrive byte-for-byte, not truncated at the first NUL.
#
# Why this is a script gate and not a tests/task_io_smoke fixture: `http_request` is a BLOCKING
# builtin (it never calls scheduler_park_on_fd*), so a Sprout server green-task and a Sprout
# http_get in the SAME process deadlock — the client freezes the pump before the server can accept.
# Until that is fixed the peer has to be a separate OS process, which is what the python3 helper
# below is. python3 is already assumed by scripts/seed_gate.sh and is present on GitHub's
# ubuntu-latest runners.
#
# The server answers two paths with the same 20-byte payload, because the client has two body paths:
#   /binary   Content-Length          -> exercises the direct copy
#   /chunked  Transfer-Encoding       -> exercises http_decode_chunked_body, which measured chunk
#                                        data with strlen and so lost everything after a NUL
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

cat > "$TMPD/server.py" <<'PY'
import socket, sys, threading

PORT = int(sys.argv[1])
# 20 bytes: a NUL at offset 4 (truncates a strlen-measured copy to 4) and a 0xFF at offset 9
# (not valid UTF-8, so this cannot be carried by a String at all).
PAYLOAD = b"AAAA\x00BBBB\xffCCCCCCCCCC"

def framed(body):
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
            + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)

def chunked(body):
    # One chunk carrying the whole payload, then the terminating 0-chunk.
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
            + b"Transfer-Encoding: chunked\r\n\r\n"
            + format(len(body), "x").encode() + b"\r\n" + body + b"\r\n0\r\n\r\n")

def handle(conn):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            part = conn.recv(4096)
            if not part:
                return
            req += part
        target = req.split(b" ")[1] if b" " in req else b"/"
        conn.sendall(chunked(PAYLOAD) if target == b"/chunked" else framed(PAYLOAD))
    finally:
        conn.close()

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(16)
print("ready", flush=True)
while True:
    conn, _ = srv.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
PY

python3 "$TMPD/server.py" "$PORT" > "$TMPD/server.out" 2>"$TMPD/server.err" &
SERVER_PID=$!

# Wait for the listener rather than sleeping a fixed amount.
for _ in $(seq 1 50); do
  if grep -q ready "$TMPD/server.out" 2>/dev/null; then break; fi
  sleep 0.1
done
if ! grep -q ready "$TMPD/server.out" 2>/dev/null; then
  echo "ERROR: helper server did not start" >&2
  cat "$TMPD/server.err" >&2
  exit 1
fi

"$COMPILER" --emit-ir "$REPO_ROOT/stdlib" --package-root "$REPO_ROOT" "$FIXTURE" > "$TMPD/fixture.ll" 2>"$TMPD/emit.err" || {
  echo "ERROR: emit-IR failed for $FIXTURE" >&2; cat "$TMPD/emit.err" >&2; exit 1; }

CLANG_EXTRA=()
if [[ "$(uname -s)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
fi
clang "$TMPD/fixture.ll" "$REPO_ROOT"/runtime/*.c "${CLANG_EXTRA[@]}" -o "$TMPD/fixture" 2>"$TMPD/link.err" || {
  echo "ERROR: link failed for $FIXTURE" >&2; tail -20 "$TMPD/link.err" >&2; exit 1; }

"$TMPD/fixture" "$PORT" > "$TMPD/run.out" 2>"$TMPD/run.err" || {
  echo "ERROR: fixture exited non-zero" >&2; cat "$TMPD/run.out" "$TMPD/run.err" >&2; exit 1; }

status=0
for label in content-length chunked; do
  got="$(grep "^$label: " "$TMPD/run.out" | head -1 | sed "s/^$label: //")"
  if [[ "$got" != "20" ]]; then
    echo "FAIL [$label]: body was $got bytes, expected 20 (truncated at the first NUL?)" >&2
    status=1
  else
    echo "PASS [$label]: 20 bytes delivered intact"
  fi
done

if [[ "$status" -ne 0 ]]; then
  echo "--- fixture output ---" >&2
  cat "$TMPD/run.out" >&2
  exit 1
fi

echo "==> http-client-binary gate: OK"
