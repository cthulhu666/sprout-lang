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

echo "==> c runtime: read_file rejects invalid UTF-8 with Err"
printf 'hello world\n' > "$TMP_DIR/utf8_good.txt"
printf 'ab\xf0' > "$TMP_DIR/utf8_bad.txt"   # trailing truncated 4-byte lead
compile read_file_utf8_validate.c "$TMP_DIR/read_file_utf8_validate" -O0 -g
"$TMP_DIR/read_file_utf8_validate" "$TMP_DIR/utf8_good.txt" "$TMP_DIR/utf8_bad.txt" > "$TMP_DIR/read_file_utf8_validate.out"
test "$(cat "$TMP_DIR/read_file_utf8_validate.out")" = "read_file-utf8-validated"

echo "==> c runtime: SIGPIPE is ignored so broken-pipe writes return EPIPE"
compile sigpipe_ignored.c "$TMP_DIR/sigpipe_ignored" -O0 -g
"$TMP_DIR/sigpipe_ignored" > "$TMP_DIR/sigpipe_ignored.out"
test "$(cat "$TMP_DIR/sigpipe_ignored.out")" = "sigpipe-ignored"

echo "==> c runtime: tcp_fail is declared noreturn (analyzer-signal guard)"
if ! grep -Eq '__attribute__\(\(noreturn\)\)[[:space:]]+static void tcp_fail\(const char\* msg\);' "$ROOT/runtime/sprout_runtime.c"; then
  echo "tcp_fail is not declared noreturn; static analysis will report phantom UAF/double-free" >&2
  exit 1
fi

echo "==> c runtime: UTF-8 walkers reject truncated multibyte with a clean panic"
if compile utf8_walker_oob.c "$TMP_DIR/utf8_walker_oob" -O1 -g -fsanitize=address,undefined; then
  :
else
  echo "  sanitizer build unavailable; using unsanitized fallback"
  compile utf8_walker_oob.c "$TMP_DIR/utf8_walker_oob" -O0 -g
fi
for sel in len char_at slice; do
  if ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0}" "$TMP_DIR/utf8_walker_oob" "$sel" \
       > "$TMP_DIR/utf8_oob.out" 2> "$TMP_DIR/utf8_oob.err"; then
    echo "  walker '$sel' did not abort on truncated UTF-8" >&2
    cat "$TMP_DIR/utf8_oob.out" >&2
    exit 1
  fi
  grep -q "truncated or malformed UTF-8" "$TMP_DIR/utf8_oob.err" || {
    echo "  walker '$sel' aborted without the expected clean panic (OOB or wrong message):" >&2
    cat "$TMP_DIR/utf8_oob.err" >&2
    exit 1
  }
done

echo "==> c runtime: char_to_str / char_from_codepoint reject invalid codepoints"
compile char_codepoint_validate.c "$TMP_DIR/char_codepoint_validate" -O0 -g
for sel in neg toobig surrogate from_neg; do
  if "$TMP_DIR/char_codepoint_validate" "$sel" > "$TMP_DIR/cc.out" 2> "$TMP_DIR/cc.err"; then
    echo "  codepoint case '$sel' did not abort" >&2
    cat "$TMP_DIR/cc.out" >&2
    exit 1
  fi
  grep -q "out of Unicode range" "$TMP_DIR/cc.err" || {
    echo "  codepoint case '$sel' aborted without the expected message:" >&2
    cat "$TMP_DIR/cc.err" >&2
    exit 1
  }
done
"$TMP_DIR/char_codepoint_validate" ok > "$TMP_DIR/cc_ok.out"
test "$(cat "$TMP_DIR/cc_ok.out")" = "char-codepoint-validated"

echo "==> c runtime: term_read_key returns fresh, validated Strings"
compile term_read_key_safety.c "$TMP_DIR/term_read_key_safety" -O0 -g
printf 'AB' | "$TMP_DIR/term_read_key_safety" alias > "$TMP_DIR/trk.out"
test "$(cat "$TMP_DIR/trk.out")" = "term-read-key-distinct"
if printf '\303' | "$TMP_DIR/term_read_key_safety" badbyte \
     > "$TMP_DIR/trk_bad.out" 2> "$TMP_DIR/trk_bad.err"; then
  echo "  term_read_key did not abort on a non-ASCII byte" >&2
  cat "$TMP_DIR/trk_bad.out" >&2
  exit 1
fi
grep -q "non-ASCII" "$TMP_DIR/trk_bad.err" || {
  echo "  term_read_key aborted without the expected message:" >&2
  cat "$TMP_DIR/trk_bad.err" >&2
  exit 1
}

echo "==> c runtime tests passed"
