#!/usr/bin/env bash
# gc_safety_check.sh against fixture C files.
#
# A brace inside a char/string literal or a comment used to shift the linter's depth count by one,
# which folded every later function into one "body": ~100 functions went unchecked while the
# linter printed "0 issues". Each fixture below puts a GC-safety defect in `bad()`, AFTER a
# function that holds a decoy brace and a safe GC call. A miscount merges the two bodies, the
# first GC call is then in `first()`, and `bad()`'s unrooted local is never looked at.
#
# GC_SAFETY_SCRIPT=<path> runs another copy of the linter (to prove these cases go red on an old
# one). GC_SAFETY_KEEP_GOING=1 reports every failing case instead of stopping at the first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${GC_SAFETY_SCRIPT:-$ROOT/scripts/gc_safety_check.sh}"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_gcsafety_test_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT
FAILED=0

fail() {
  echo "FAIL: $*" >&2
  FAILED=1
  [[ "${GC_SAFETY_KEEP_GOING:-0}" == 1 ]] || exit 1
}

# run <name> [args...]: lint $TMPD/<name>.c. The file is also copied to runtime/sprout_runtime.c
# under its own directory, which is where a linter without GC_SAFETY_RUNTIME looks.
# Output in $OUT, exit code in $RC.
run() {
  local name="$1"; shift
  local dir="$TMPD/run_$name"
  mkdir -p "$dir/runtime"
  cp "$TMPD/$name.c" "$dir/runtime/sprout_runtime.c"
  RC=0
  OUT="$(cd "$dir" && GC_SAFETY_RUNTIME="$TMPD/$name.c" bash "$SCRIPT" "$@" 2>&1)" || RC=$?
}

# fixture <name> <decoy lines> <bad body lines>: `first()` holds the decoy and a safe GC call.
# `bad()` holds <bad body>; the default body is the defect (unrooted char* used after the GC call).
BAD_DEFECT='    char *p = make_string();
    sprout_gc_maybe_collect_threshold();
    use_string(p);'
BAD_ROOTED='    char *p = make_string();
    SPROUT_GC_PUSH_PTR_LOCAL(p);
    sprout_gc_maybe_collect_threshold();
    use_string(p);'
fixture() {
  local name="$1" decoy="$2" bad="$3"
  {
    printf '%s\n' '#include <stdio.h>' ''
    printf 'int first(int c) {\n%s\n    sprout_gc_maybe_collect_threshold();\n    return 0;\n}\n\n' "$decoy"
    printf 'void bad(void) {\n%s\n}\n\n' "$bad"
    printf 'int last(void) {\n    return 0;\n}\n'
  } >"$TMPD/$name.c"
}

# defs <name>: function definitions in the fixture (every one starts at column 0 with a type).
defs() { grep -cE '^(void|int) [a-z_]+\(.*\) \{$' "$TMPD/$1.c"; }

# expect_flagged <case>: --strict exits 1 and names bad() and its variable.
expect_flagged() {
  [[ "$RC" == 1 ]] || { fail "$1: exit $RC, want 1: $OUT"; return 0; }
  grep -qF 'bad(): "p"' <<<"$OUT" || { fail "$1: output lacks bad(): \"p\": $OUT"; return 0; }
  echo "ok: $1"
}

# (0) the defect alone, no decoy: any linter flags it, so the fixtures below test the decoys.
fixture plain '    puts("plain");' "$BAD_DEFECT"
run plain --strict
expect_flagged "(0) plain defect is flagged"

# (a) char literals holding a brace
fixture char_open "    if (c == '{') return 1;" "$BAD_DEFECT"
run char_open --strict
expect_flagged "(a) '{' char literal does not hide the next function"

# A '}' literal ends a body early, so it has to sit inside the defective function to hide it.
fixture char_close '    puts("x");' "    int k = '}';
$BAD_DEFECT"
run char_close --strict
expect_flagged "(a) '}' char literal does not end the function early"

# (b) every other place a brace can sit without being code
fixture string_open '    puts("{");' "$BAD_DEFECT"
run string_open --strict
expect_flagged "(b) brace in a string literal"

fixture string_escaped_quote '    puts("a\"{");' "$BAD_DEFECT"
run string_escaped_quote --strict
expect_flagged "(b) brace after an escaped quote in a string"

fixture line_comment '    // { open
    puts("x"); // }}} close' "$BAD_DEFECT"
run line_comment --strict
expect_flagged "(b) brace in a // comment"

fixture block_comment_inline '    /* { */ puts("x");' "$BAD_DEFECT"
run block_comment_inline --strict
expect_flagged "(b) brace in a one-line /* */ comment"

fixture block_comment_multiline '    /* start {
       { still a comment
       } */
    puts("x");' "$BAD_DEFECT"
run block_comment_multiline --strict
expect_flagged "(b) braces in a multi-line /* */ comment"

fixture escaped_quote_char "    if (c == '\\'' || c == '{') return 1;" "$BAD_DEFECT"
run escaped_quote_char --strict
expect_flagged "(b) '\\'' escape before a brace literal"

fixture escaped_backslash_char "    if (c == '\\\\' || c == '{') return 1;" "$BAD_DEFECT"
run escaped_backslash_char --strict
expect_flagged "(b) '\\\\' escape before a brace literal"

fixture apostrophe_in_comment "    // don't { open
    /* it's { */ puts(\"x\");" "$BAD_DEFECT"
run apostrophe_in_comment --strict
expect_flagged "(b) apostrophe inside a comment does not open a literal"

# (c) drift is a failure, strict or not. The decoys are gone, so the miscount is real.
# Missing close: `next()` starts at column 0 while `broken()` is still open.
cat >"$TMPD/missing_close.c" <<'EOF'
int broken(int c) {
    if (c) {
        return 1;
    return 0;
}

int next(void) {
    return 0;
}
EOF
for flag in --strict --verbose; do
  run missing_close "$flag"
  if [[ "$RC" == 2 ]] && grep -qF 'line 7 starts next() while broken() (line 1) is still open' <<<"$OUT"; then
    echo "ok: (c) missing close brace fails with exit 2 ($flag)"
  else
    fail "(c) missing close ($flag): exit $RC, want 2 naming both functions: $OUT"
  fi
done

# Extra close: depth goes negative.
cat >"$TMPD/extra_close.c" <<'EOF'
int extra(void) {
    return 0;
}}
EOF
run extra_close --strict
if [[ "$RC" == 2 ]] && grep -qF 'depth -1 inside extra() (line 1)' <<<"$OUT"; then
  echo "ok: (c) extra close brace fails with exit 2"
else
  fail "(c) extra close: exit $RC, want 2 naming extra(): $OUT"
fi

# End of file inside a function.
cat >"$TMPD/eof_open.c" <<'EOF'
int fine(void) {
    return 0;
}

int open_at_eof(void) {
    if (1) {
        return 0;
    }
EOF
run eof_open --strict
if [[ "$RC" == 2 ]] && grep -qF 'end of file reached inside open_at_eof() (line 5)' <<<"$OUT"; then
  echo "ok: (c) function open at end of file fails with exit 2"
else
  fail "(c) eof: exit $RC, want 2 naming open_at_eof(): $OUT"
fi

# (d) clean: every decoy at once, `bad()` rooted. No issue, and every definition is counted.
fixture clean "    if (c == '{') return 1;
    if (c == '\\'' || c == '}') return 1;
    puts(\"{ \\\" {\");
    // { open
    /* {
       { */
    return 2; /* } */" "$BAD_ROOTED"
for flag in --strict --verbose; do
  run clean "$flag"
  want="$(defs clean) runtime functions checked, 0 issues"
  if [[ "$RC" == 0 ]] && grep -qF "$want" <<<"$OUT"; then
    echo "ok: (d) clean fixture: $want ($flag)"
  else
    fail "(d) clean ($flag): exit $RC, want '$want': $OUT"
  fi
done

# Without --strict a finding only warns; drift is still fatal (checked above with --verbose).
run plain
if [[ "$RC" == 0 ]] && grep -qF 'WARN' <<<"$OUT"; then
  echo "ok: findings without --strict warn and exit 0"
else
  fail "non-strict: exit $RC, want 0 with WARN: $OUT"
fi

# A missing runtime file is exit 2. (An old linter ignores GC_SAFETY_RUNTIME, so skip the case there.)
if [[ -z "${GC_SAFETY_SCRIPT:-}" ]]; then
  RC=0
  OUT="$(GC_SAFETY_RUNTIME="$TMPD/does_not_exist.c" bash "$SCRIPT" --strict 2>&1)" || RC=$?
  if [[ "$RC" == 2 ]] && grep -qF 'not found' <<<"$OUT"; then
    echo "ok: missing runtime file exits 2"
  else
    fail "missing file: exit $RC, want 2: $OUT"
  fi
fi

exit "$FAILED"
