#!/usr/bin/env bash
# scripts/http_tls_gate.sh
#
# The only automated coverage of the HTTPS client path.
#
# `http_request_tls` is `#ifdef __APPLE__`, so the Linux CI job cannot compile it, never mind run
# it — the TLS client had no automated coverage at all until this gate. That mattered more after
# the client was made non-blocking: the rewrite replaced SecureTransport's `errSSLWouldBlock` retry
# loops (safe only because a blocking `recv` underneath did the waiting) with parks in all three
# loops — handshake, write and read. A regression there is a failed request, a hang, or a pegged
# core, and nothing was watching for any of them.
#
# HERMETIC BY CONSTRUCTION. The peer is `openssl s_server` holding a leaf issued by a CA generated
# here and thrown away at exit, with SPROUT_HTTP_CA_CERT pointing at that CA. No public endpoint,
# no DNS, no dependence on anyone else's certificate lifetime or uptime — the gate behaves the same
# on a laptop with no network as on a CI runner.
#
# TWO RUNS, because the positive one alone proves nothing:
#   1. WITH the anchor  -> must succeed and report a non-empty body.
#   2. WITHOUT it       -> must FAIL. A trust evaluation that accepted everything would sail
#                          through run 1; only run 2 can tell "the anchor was used" from "the
#                          certificate was never really checked". Measured: -9807
#                          (errSSLXCertChainInvalid).
#
# CERTIFICATE SHAPE IS LOAD-BEARING, and the obvious shortcut does not work. A single self-signed
# certificate used as both leaf and anchor is REJECTED by macOS with "tls certificate verification
# failed": Apple requires a TLS server certificate to carry an ExtendedKeyUsage of serverAuth,
# which `openssl req -x509` does not add. Hence a real two-level chain — a CA with
# basicConstraints CA:TRUE, and a leaf with serverAuth plus a subjectAltName (a CN alone has not
# been accepted for years). The anchor is converted to DER because SecCertificateCreateWithData
# takes DER, not PEM.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPILER="${SPROUT_STAGE1:-$REPO_ROOT/build/compile_driver_bin_stage1}"
PORT="${SPROUT_TLS_GATE_PORT:-28444}"
FIXTURE="$REPO_ROOT/tests/http_client/tls_localhost.spr"

# The client only HAS a TLS implementation on Apple platforms; elsewhere http_request returns
# "https unsupported on this platform". Skipping (rather than failing) keeps this gate safe to list
# in `just gate`, which developers and the Linux container both run.
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "==> http-tls gate: SKIP (the TLS client path is macOS-only)"
  exit 0
fi

if [[ ! -x "$COMPILER" ]]; then
  echo "ERROR: compiler not found at $COMPILER" >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl not found; this gate needs its s_server" >&2
  exit 1
fi

TMPD="$(mktemp -d /tmp/sprout_tls_gate_XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$TMPD"
}
trap cleanup EXIT

# ── the private CA and its server leaf ────────────────────────────────────────────────────────
# 2 days of validity: long enough for any run, short enough that a stray copy is inert. Both
# OpenSSL 3 and the LibreSSL that ships as /usr/bin/openssl accept these flags, so the gate does
# not care which one a runner puts first on PATH.
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$TMPD/ca.key" -out "$TMPD/ca.pem" -days 2 \
  -subj "/CN=Sprout Test CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" 2>"$TMPD/ca.err" || {
  echo "ERROR: could not generate the test CA" >&2; cat "$TMPD/ca.err" >&2; exit 1; }

openssl req -newkey rsa:2048 -nodes -keyout "$TMPD/leaf.key" -out "$TMPD/leaf.csr" \
  -subj "/CN=localhost" 2>"$TMPD/leaf.err" || {
  echo "ERROR: could not generate the server key" >&2; cat "$TMPD/leaf.err" >&2; exit 1; }

# serverAuth and a SAN are both REQUIRED by macOS; without either, the handshake fails at trust
# evaluation rather than at anything this gate is trying to test.
cat > "$TMPD/leaf.ext" <<'EXT'
subjectAltName=DNS:localhost
extendedKeyUsage=serverAuth
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
EXT
openssl x509 -req -in "$TMPD/leaf.csr" -CA "$TMPD/ca.pem" -CAkey "$TMPD/ca.key" -CAcreateserial \
  -out "$TMPD/leaf.pem" -days 2 -extfile "$TMPD/leaf.ext" -sha256 2>"$TMPD/sign.err" || {
  echo "ERROR: could not sign the server certificate" >&2; cat "$TMPD/sign.err" >&2; exit 1; }

# DER, not PEM: SecCertificateCreateWithData rejects PEM, and the runtime feeds this file to it
# verbatim (see tls_configure_peer_trust).
openssl x509 -in "$TMPD/ca.pem" -outform der -out "$TMPD/ca.der"

# ── build the fixture ─────────────────────────────────────────────────────────────────────────
"$COMPILER" --emit-ir "$REPO_ROOT/stdlib" --package-root "$REPO_ROOT" "$FIXTURE" \
  > "$TMPD/fixture.ll" 2>"$TMPD/emit.err" || {
  echo "ERROR: emit-IR failed for $FIXTURE" >&2; cat "$TMPD/emit.err" >&2; exit 1; }
clang "$TMPD/fixture.ll" "$REPO_ROOT"/runtime/*.c -framework Security -framework CoreFoundation \
  -o "$TMPD/fixture" 2>"$TMPD/link.err" || {
  echo "ERROR: link failed for $FIXTURE" >&2; tail -20 "$TMPD/link.err" >&2; exit 1; }

# ── serve ─────────────────────────────────────────────────────────────────────────────────────
# `-www` makes s_server answer with a real HTTP response (status line, headers, an HTML body), so
# no separate HTTP server is needed and the client parses a genuine response.
openssl s_server -www -cert "$TMPD/leaf.pem" -key "$TMPD/leaf.key" -accept "$PORT" \
  > "$TMPD/server.log" 2>&1 &
SERVER_PID=$!

# Wait for the LISTENER rather than sleeping a fixed amount.
ready=0
for _ in $(seq 1 100); do
  if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then ready=1; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
  sleep 0.1
done
if [[ "$ready" != 1 ]]; then
  echo "ERROR: openssl s_server did not start on port $PORT" >&2
  cat "$TMPD/server.log" >&2
  exit 1
fi

URL="https://localhost:$PORT/"
status=0

# ── run 1: with the anchor, the request must succeed and the pump must stay live ──────────────
if ! SPROUT_HTTP_CA_CERT="$TMPD/ca.der" perl -e 'alarm 30; exec @ARGV' "$TMPD/fixture" "$URL" \
     > "$TMPD/trusted.out" 2>&1; then
  echo "FAIL [trusted]: the fixture did not complete (hang or crash)" >&2
  status=1
fi
if grep -q "^tls-body-bytes " "$TMPD/trusted.out"; then
  echo "PASS [trusted]: $(grep '^tls-body-bytes ' "$TMPD/trusted.out")"
else
  echo "FAIL [trusted]: expected a non-empty body over TLS" >&2
  status=1
fi
if grep -q "^tls-scheduler-live$" "$TMPD/trusted.out"; then
  echo "PASS [liveness]: sibling timer fired while the TLS request was in flight"
else
  echo "FAIL [liveness]: the scheduler was blocked during the TLS request" >&2
  status=1
fi

# ── run 2: without the anchor, the request MUST fail ──────────────────────────────────────────
# The negative control. If this passes, run 1 proved nothing about trust evaluation.
if ! perl -e 'alarm 30; exec @ARGV' "$TMPD/fixture" "$URL" > "$TMPD/untrusted.out" 2>&1; then
  echo "FAIL [untrusted]: the fixture did not complete (hang or crash)" >&2
  status=1
fi
if grep -q "^tls-request-FAILED" "$TMPD/untrusted.out"; then
  echo "PASS [untrusted]: private CA correctly rejected without the anchor"
else
  echo "FAIL [untrusted]: a certificate from an unknown CA was ACCEPTED — trust evaluation is not happening" >&2
  status=1
fi

if [[ "$status" -ne 0 ]]; then
  echo "--- trusted run ---" >&2;   cat "$TMPD/trusted.out" >&2
  echo "--- untrusted run ---" >&2; cat "$TMPD/untrusted.out" >&2
  echo "--- server log ---" >&2;    tail -20 "$TMPD/server.log" >&2
  exit 1
fi

echo "==> http-tls gate: OK"
