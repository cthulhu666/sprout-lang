#!/usr/bin/env bash
# scripts/twelve_dollar_example.sh
#
# Builds examples/twelve_dollar_server — the app plus its vendored sqlite driver — and checks every
# route once over HTTP against a throwaway database. compile-examples cannot cover it: it links the
# runtime alone, and this app also needs the driver's C shim and the system libsqlite3.
#
# Needs clang, sqlite3 (to make the database), curl and openssl (to sign a JWT).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EX="$REPO_ROOT/examples/twelve_dollar_server"
COMPILER="${SPROUT_STAGE1:-$REPO_ROOT/build/compile_driver_bin_stage1}"
PORT="${SPROUT_TWELVE_PORT:-28775}"
SECRET="twelve-dollar-example-secret"
BASE="http://127.0.0.1:$PORT"

if [[ ! -x "$COMPILER" ]]; then
  echo "ERROR: compiler not found at $COMPILER" >&2
  exit 1
fi

TMPD="$(mktemp -d /tmp/sprout_twelve_XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$TMPD"
}
trap cleanup EXIT

fail() {
  echo "twelve-dollar-example: $*" >&2
  if [[ -s "$TMPD/server.err" ]]; then echo "--- server stderr ---" >&2; cat "$TMPD/server.err" >&2; fi
  exit 1
}

# The runtime's TLS needs these on macOS; Linux needs nothing extra.
EXTRA=()
[[ "$(uname)" == Darwin ]] && EXTRA=(-framework Security -framework CoreFoundation)

# --package-root is the example folder, so `app.*` and the vendored `sqlite.*` resolve unchanged.
"$COMPILER" --emit-ir "$REPO_ROOT/stdlib" --package-root "$EX" "$EX/app/main.sprout" \
  > "$TMPD/server.ll" 2>"$TMPD/emit.err" || { cat "$TMPD/emit.err" >&2; fail "emit-IR failed"; }
clang "$TMPD/server.ll" "$REPO_ROOT"/runtime/*.c "$EX/src/sqlite_ffi.c" -O2 ${EXTRA[@]+"${EXTRA[@]}"} \
  -lsqlite3 -o "$TMPD/server" 2>"$TMPD/link.err" || { cat "$TMPD/link.err" >&2; fail "link failed"; }

sqlite3 "$TMPD/feed.db" < "$EX/schema.sql"
sqlite3 "$TMPD/feed.db" "INSERT INTO users (id, username) VALUES (1, 'ada'); INSERT INTO posts (id, user_id, body) VALUES (1, 1, 'hello from sprout');"

SQLITE_PATH="$TMPD/feed.db" JWT_SECRET="$SECRET" HOST=127.0.0.1 PORT="$PORT" \
  "$TMPD/server" 2>"$TMPD/server.err" &
SERVER_PID=$!
for _ in $(seq 50); do
  curl -sf "$BASE/health" > /dev/null 2>&1 && break
  kill -0 "$SERVER_PID" 2>/dev/null || fail "server exited at startup"
  sleep 0.1
done

# HS256, signed the way the challenge's seed tokens are.
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
header=$(printf '{"alg":"HS256","typ":"JWT"}' | b64url)
payload=$(printf '{"sub":"1","username":"ada","exp":%d}' "$(( $(date +%s) + 3600 ))" | b64url)
sig=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -hmac "$SECRET" -binary | b64url)
TOKEN="$header.$payload.$sig"

check() {  # check <label> <want-status> <want-substring> <curl args...>
  local label="$1" want="$2" body="$3"
  shift 3
  local got
  got=$(curl -s -o "$TMPD/body" -w '%{http_code}' "$@") || fail "[$label] curl failed"
  if [[ "$got" != "$want" ]] || ! grep -qF -- "$body" "$TMPD/body"; then
    fail "[$label] want $want with '$body', got $got: $(cat "$TMPD/body")"
  fi
}

auth=(-H "Authorization: Bearer $TOKEN")
check health 200 '"status":"ok"' "$BASE/health"
check feed 200 '"body":"hello from sprout"' "$BASE/feed"
check post 200 '{"post":{"id":1,' "$BASE/posts/1"
check post-missing 404 'post not found' "$BASE/posts/99"
check post-bad-id 400 'invalid post id' "$BASE/posts/abc"
check like-no-token 401 'missing bearer token' -X POST "$BASE/posts/1/like"
check like-bad-token 401 'invalid or expired token' -X POST -H "Authorization: Bearer $header.$payload.AAAA" "$BASE/posts/1/like"
check like 201 '"already_liked":false' -X POST "${auth[@]}" "$BASE/posts/1/like"
check like-again 200 '"already_liked":true' -X POST "${auth[@]}" "$BASE/posts/1/like"
check like-missing 404 'post not found' -X POST "${auth[@]}" "$BASE/posts/99/like"
check create-empty 400 'body is required' -X POST "${auth[@]}" -d '{"body":"  "}' "$BASE/posts"
# Non-ASCII text: the driver reads plain UTF-8 column text in one copy, everything else by bytes.
check create 201 '"author":"ada"' -X POST "${auth[@]}" -d '{"body":"zażółć gęślą jaźń"}' "$BASE/posts"
check read-back 200 'zażółć gęślą jaźń' "$BASE/posts/2"
check like-count 200 '"like_count":1' "$BASE/posts/1"

# Keep-alive: curl reuses one connection for two URLs only if the server kept it open.
connects=$(curl -s -o /dev/null -o /dev/null -w '%{num_connects} ' "$BASE/health" "$BASE/feed")
[[ "$connects" == "1 0 " ]] || fail "[keep-alive] want connects '1 0 ', got '$connects'"

echo "==> twelve-dollar-example ✓ (build, 14 routes, keep-alive)"
