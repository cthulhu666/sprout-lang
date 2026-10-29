#!/usr/bin/env bash
# runtime_word_casts.py against copies of runtime/ with one defect planted at a time.
# Each case checks the exit code AND that the message names the offending decl.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/runtime_word_casts.py"
CLANG="${CLANG:-clang}"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_wordcasts_test_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cp -R "$ROOT/runtime" "$TMPD/runtime"
cp "$ROOT/runtime/WORD_CASTS_BASELINE" "$TMPD/baseline"
cp "$ROOT/runtime/ADDRESS_CASTS" "$TMPD/addr"
RT="$TMPD/runtime"

# run <runtime-dir> <baseline> <address-casts>: output in $OUT, exit code in $RC.
run() {
  RC=0
  OUT="$(python3 -I "$CHECK" "$CLANG" "$1" "$2" "$3" 2>&1)" || RC=$?
}

# expect_fail <case> <needle>: the last run exited 1 and printed <needle>.
expect_fail() {
  [[ "$RC" == 1 ]] || fail "$1: exit $RC, want 1: $OUT"
  grep -qF -- "$2" <<<"$OUT" || fail "$1: output lacks '$2': $OUT"
  echo "ok: $1"
}

# plant <file> <exact line> <inserted line>: insert after the first line equal to <exact line>.
plant() {
  local tmp="$1.tmp"
  awk -v at="$2" -v add="$3" '{ print } !done && $0 == at { print add; done = 1 }' "$1" >"$tmp"
  cmp -s "$1" "$tmp" && fail "plant: no line '$2' in $1"
  mv "$tmp" "$1"
}

# (a) the real tree passes.
run "$RT" "$TMPD/baseline" "$TMPD/addr"
[[ "$RC" == 0 ]] || fail "(a) real tree: exit $RC: $OUT"
grep -q '^runtime-word-casts: ok' <<<"$OUT" || fail "(a) no ok line: $OUT"
echo "ok: (a) real tree passes"

# (b) the mutant: char_to_string is an exported String producer with no cast today.
plant "$RT/sprout_runtime.c" 'long long char_to_string(long long ch) {' \
  '  return (long long)(uintptr_t)"x";'
run "$RT" "$TMPD/baseline" "$TMPD/addr"
expect_fail "(b) mutant in char_to_string" 'runtime/sprout_runtime.c:char_to_string has 1 pointer-to-integer cast(s) and is not in the baseline'
cp "$ROOT/runtime/sprout_runtime.c" "$RT/sprout_runtime.c"

# (c) one more cast in a function the baseline already lists.
plant "$RT/sprout_runtime.c" 'static long long box_ptr(void* p) {' \
  '  uintptr_t extra_ = (uintptr_t)p; (void)extra_;'
run "$RT" "$TMPD/baseline" "$TMPD/addr"
expect_fail "(c) count rise in box_ptr" 'runtime/sprout_runtime.c:box_ptr has 2 pointer-to-integer cast(s), baseline allows 1'
cp "$ROOT/runtime/sprout_runtime.c" "$RT/sprout_runtime.c"

# (d) a baseline entry with an inflated count, then one for a decl that has no casts.
sed 's/^runtime\/sprout_runtime.c:box_ptr:1$/runtime\/sprout_runtime.c:box_ptr:2/' "$ROOT/runtime/WORD_CASTS_BASELINE" >"$TMPD/baseline_inflated"
cmp -s "$ROOT/runtime/WORD_CASTS_BASELINE" "$TMPD/baseline_inflated" && fail "(d) could not inflate box_ptr"
run "$RT" "$TMPD/baseline_inflated" "$TMPD/addr"
expect_fail "(d) inflated box_ptr entry" 'runtime/sprout_runtime.c:box_ptr now has 1 pointer-to-integer cast(s), baseline lists 2; lower the baseline entry'
{ cat "$ROOT/runtime/WORD_CASTS_BASELINE"; echo 'runtime/sprout_runtime.c:no_such_fn:1'; } >"$TMPD/baseline_invented"
run "$RT" "$TMPD/baseline_invented" "$TMPD/addr"
expect_fail "(d) invented entry no_such_fn" 'runtime/sprout_runtime.c:no_such_fn now has 0 pointer-to-integer cast(s), baseline lists 1; delete the baseline entry'

# (e) a long long function in ADDRESS_CASTS, where its casts could reach a Sprout word.
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo 'box_ptr  # wrong: returns long long'; } >"$TMPD/addr_ll"
run "$RT" "$TMPD/baseline" "$TMPD/addr_ll"
expect_fail "(e) long long box_ptr in ADDRESS_CASTS" 'runtime/sprout_runtime.c:box_ptr returns long long'

# (f) ADDRESS_CASTS names with no cast, and with no definition.
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo 'str_len  # defined, no pointer-to-integer cast'; } >"$TMPD/addr_nocast"
run "$RT" "$TMPD/baseline" "$TMPD/addr_nocast"
expect_fail "(f) str_len has no cast" 'ADDRESS_CASTS: str_len has no pointer-to-integer cast'
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo 'no_such_fn  # not defined'; } >"$TMPD/addr_undef"
run "$RT" "$TMPD/baseline" "$TMPD/addr_undef"
expect_fail "(f) no_such_fn is not defined" 'ADDRESS_CASTS: no_such_fn is not defined'

# (g)-(i) platform tags.  THIS is the host python sees; OTHER is the one it ignores.
if [[ "$(uname -s)" == Darwin ]]; then THIS=darwin OTHER=linux; else THIS=linux OTHER=darwin; fi
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo "str_len $THIS  # defined, no cast"; } >"$TMPD/addr_tag_this"
run "$RT" "$TMPD/baseline" "$TMPD/addr_tag_this"
expect_fail "(g) str_len tagged $THIS is stale" 'ADDRESS_CASTS: str_len has no pointer-to-integer cast'
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo "str_len $OTHER  # defined, no cast"; } >"$TMPD/addr_tag_other"
run "$RT" "$TMPD/baseline" "$TMPD/addr_tag_other"
[[ "$RC" == 0 ]] || fail "(h) str_len tagged $OTHER: exit $RC: $OUT"
echo "ok: (h) entry tagged $OTHER is ignored"
{ cat "$ROOT/runtime/ADDRESS_CASTS"; echo 'str_len beos  # bad tag'; } >"$TMPD/addr_tag_bad"
run "$RT" "$TMPD/baseline" "$TMPD/addr_tag_bad"
expect_fail "(i) unknown tag" 'malformed entry'

echo "test-runtime-word-casts: ok"
