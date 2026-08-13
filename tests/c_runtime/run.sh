#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sprout-c-runtime-tests.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

CLANG_EXTRA=()
if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
fi

# Link EVERY runtime translation unit, not just sprout_runtime.c. When the runtime was
# split (sprout_scheduler.c, sprout_poll.c) this harness was not updated, so every case
# here failed to link — "symbol(s) not found: _scheduler_park_on_fd" and friends — and
# nothing noticed, because `just c-runtime-test` is referenced by no aggregate recipe and
# no CI job. Every assertion in this directory was silently unrunnable, including the one
# that documented term_read_key's abort contract.
compile() {
  local src="$1"
  local out="$2"
  shift 2
  clang "$@" "$ROOT"/runtime/*.c "$ROOT/tests/c_runtime/$src" "${CLANG_EXTRA[@]}" -o "$out"
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
# A complete multi-byte keypress must arrive as one whole character. Before this,
# term_read_key aborted on any byte >= 0x80, so typing an accented character killed
# the REPL and lost the session.
printf '\303\251' | "$TMP_DIR/term_read_key_safety" multibyte > "$TMP_DIR/trk_mb.out"
test "$(cat "$TMP_DIR/trk_mb.out")" = "term-read-key-multibyte"
# 3- and 4-byte sequences back to back: the continuation count must come from the
# lead byte, not be assumed to be one.
printf '\346\227\245\360\237\214\261' | "$TMP_DIR/term_read_key_safety" wide > "$TMP_DIR/trk_wide.out"
test "$(cat "$TMP_DIR/trk_wide.out")" = "term-read-key-wide"
# A truncated sequence (lead byte, then EOF) must yield U+FFFD, NOT abort and NOT an
# invalid 1-byte String. Exit status is checked as well as output, since the previous
# contract here was a non-zero exit.
printf '\303' | "$TMP_DIR/term_read_key_safety" badbyte > "$TMP_DIR/trk_bad.out"
test "$(cat "$TMP_DIR/trk_bad.out")" = "term-read-key-replacement"
# An invalid continuation must also yield U+FFFD, and must not swallow the byte that
# follows: that byte is the next keystroke.
printf '\303A' | "$TMP_DIR/term_read_key_safety" badcont > "$TMP_DIR/trk_cont.out"
test "$(cat "$TMP_DIR/trk_cont.out")" = "term-read-key-badcont-keeps-next"

echo "==> c runtime: EMFILE accept sheds the backlog instead of hot-spinning"
# Deterministic coverage of finding 9. tcp_accept's EMFILE path used to park-and-retry without
# draining — a 100% CPU spin, since accept() under EMFILE does not dequeue the pending connection.
# This drives accept_shed_backlog directly under a lowered RLIMIT_NOFILE (no scheduler, no timing)
# and asserts the shed drains the backlog and re-arms the reserve. It SKIPs cleanly (still exit 0,
# marker "emfile-shed-skipped") if the sandbox does not enforce the rlimit; the assertion path prints
# "emfile-shed-drained". The end-to-end survival half is tests/task_io_smoke/http_accept_exhaustion.spr.
compile emfile_accept_shed.c "$TMP_DIR/emfile_accept_shed" -O0 -g
"$TMP_DIR/emfile_accept_shed" > "$TMP_DIR/emfile_accept_shed.out"
grep -Eq '^emfile-shed-(drained|skipped)$' "$TMP_DIR/emfile_accept_shed.out" || {
  echo "emfile shed test did not reach a known outcome:" >&2
  cat "$TMP_DIR/emfile_accept_shed.out" >&2
  exit 1
}

echo "==> c runtime: accept shed is bounded per call so it cannot freeze the pump"
# The shed runs on the single scheduler thread with no park in it, so an unbounded drain blocks every
# other green task — including the handlers whose completion frees the descriptors it is waiting on.
# See the file header for why this needs no EMFILE and why it does not hardcode the cap.
compile accept_shed_bounded.c "$TMP_DIR/accept_shed_bounded" -O0 -g
"$TMP_DIR/accept_shed_bounded" > "$TMP_DIR/accept_shed_bounded.out"
grep -Eq '^accept-shed-bounded(-skipped)?$' "$TMP_DIR/accept_shed_bounded.out" || {
  echo "accept shed bound test did not reach a known outcome:" >&2
  cat "$TMP_DIR/accept_shed_bounded.out" >&2
  exit 1
}
# The behavioural test above deliberately asserts the PROPERTY, not the number. Pin the constant's
# existence separately, so removing the bound cannot pass by being retuned to something enormous.
grep -q '#define ACCEPT_SHED_MAX_PER_CALL' "$ROOT/runtime/sprout_runtime.c" || {
  echo "ACCEPT_SHED_MAX_PER_CALL is gone; the accept shed is unbounded again" >&2
  exit 1
}

echo "==> c runtime: with_timeout re-classifies a runnable body instead of awaiting it unbounded"
# Source invariant, because the behaviour is not reachable from a test. __await_deadline's `in_rq`
# branch is entered only when the deadline timer and the child's fd readiness land in the SAME poll
# batch AND the batch lists the timer first — a sub-microsecond window no test can schedule.
#
# The old code read `in_rq` as "one tick from done" and re-awaited the child with no timer left, so a
# body doing `read A; read B` hung FOREVER whenever A's readiness hit that window: with_timeout's one
# guarantee, silently void. The fix yields (rq_push(owner)) and re-classifies, so the child is either
# done or parked-and-droppable by the next visit. That rq_push is the whole fix and appears nowhere
# else in the scheduler, which makes it a precise witness.
grep -q 'rq_push(owner);' "$ROOT/runtime/sprout_scheduler.c" || {
  echo "__await_deadline no longer yields before re-classifying a runnable child;" >&2
  echo "with_timeout can hang past its deadline again (green-threads review, finding 1)" >&2
  exit 1
}

echo "==> c runtime: the poller treats EINTR as retryable, not fatal"
# Both backends used to sprout_fail on ANY negative return from the wait, EINTR included — a delivered
# signal taking down the whole process and every in-flight connection with it. One guard per backend.
test "$(grep -c 'errno == EINTR' "$ROOT/runtime/sprout_poll.c")" = "2" || {
  echo "sprout_poll_wait does not handle EINTR on both backends (kqueue + epoll)" >&2
  exit 1
}

echo "==> c runtime tests passed"
