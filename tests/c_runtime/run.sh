#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sprout-c-runtime-tests.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

CLANG_EXTRA=()
if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
fi

compile() {
  local src="$1"
  local out="$2"
  shift 2
  clang "$@" "$ROOT/runtime/sprout_runtime.c" "$ROOT/tests/c_runtime/$src" "${CLANG_EXTRA[@]}" -o "$out"
}

echo "==> c runtime: read_file keeps Ok payload alive under stress GC"
printf "abcdef" > "$TMP_DIR/input.txt"
if compile read_file_gc.c "$TMP_DIR/read_file_gc_asan" -O1 -g -fsanitize=address,undefined; then
  ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0}" "$TMP_DIR/read_file_gc_asan" "$TMP_DIR/input.txt" > "$TMP_DIR/read_file_gc.out"
else
  echo "  sanitizer build unavailable; using unsanitized fallback"
  compile read_file_gc.c "$TMP_DIR/read_file_gc" -O0 -g
  "$TMP_DIR/read_file_gc" "$TMP_DIR/input.txt" > "$TMP_DIR/read_file_gc.out"
fi
test "$(cat "$TMP_DIR/read_file_gc.out")" = "6"

echo "==> c runtime: read_file Err payload prints as a string"
compile read_file_error_print.c "$TMP_DIR/read_file_error_print" -O0 -g
"$TMP_DIR/read_file_error_print" > "$TMP_DIR/read_file_error_print.out"
grep -q '^Err(' "$TMP_DIR/read_file_error_print.out"
if grep -Eq '^Err\([0-9]+\)$' "$TMP_DIR/read_file_error_print.out"; then
  echo "read_file Err payload printed as a raw pointer:" >&2
  cat "$TMP_DIR/read_file_error_print.out" >&2
  exit 1
fi

echo "==> c runtime: constructor metadata fails before table overflow"
compile ctor_meta_capacity.c "$TMP_DIR/ctor_meta_capacity" -O0 -g
if "$TMP_DIR/ctor_meta_capacity" > "$TMP_DIR/ctor_meta_capacity.out" 2> "$TMP_DIR/ctor_meta_capacity.err"; then
  echo "constructor registration unexpectedly succeeded past capacity" >&2
  exit 1
fi
grep -q "constructor metadata table full" "$TMP_DIR/ctor_meta_capacity.err"

echo "==> c runtime: process grow buffer keeps old pointer on realloc failure"
grep -q 'char\* grown = (char\*)realloc' "$ROOT/runtime/sprout_runtime.c"
if grep -q 'b->data = (char\*)realloc' "$ROOT/runtime/sprout_runtime.c"; then
  echo "sprout_growbuf_append overwrites b->data with unchecked realloc" >&2
  exit 1
fi

echo "==> c runtime: SIGPIPE is ignored so broken-pipe writes return EPIPE"
compile sigpipe_ignored.c "$TMP_DIR/sigpipe_ignored" -O0 -g
"$TMP_DIR/sigpipe_ignored" > "$TMP_DIR/sigpipe_ignored.out"
test "$(cat "$TMP_DIR/sigpipe_ignored.out")" = "sigpipe-ignored"

echo "==> c runtime: tcp_fail is declared noreturn (analyzer-signal guard)"
if ! grep -Eq '__attribute__\(\(noreturn\)\)[[:space:]]+static void tcp_fail\(const char\* msg\);' "$ROOT/runtime/sprout_runtime.c"; then
  echo "tcp_fail is not declared noreturn; static analysis will report phantom UAF/double-free" >&2
  exit 1
fi

echo "==> c runtime tests passed"
