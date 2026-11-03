#!/usr/bin/env bash
# check_extern_signatures.sh against throwaway fixture roots (SPROUT_EXTERN_CHECK_ROOT).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/check_extern_signatures.sh"
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/sprout_extern_sig_XXXXXX")
trap 'rm -rf "$TMPD"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# fixture <name> <sprout-file> <sprout-text> [<c-text>]: builds $TMPD/<name> and runs the check.
# Output in $OUT, exit code in $RC.
fixture() {
  local name="$1" file="$2" text="$3" ctext="${4:-}"
  local dir="$TMPD/$name"
  mkdir -p "$dir/stdlib" "$dir/runtime" "$(dirname "$dir/$file")"
  printf '%s\n' "$text" > "$dir/$file"
  printf '%s\n' "$ctext" > "$dir/runtime/rt.c"
  RC=0
  OUT="$(SPROUT_EXTERN_CHECK_ROOT="$dir" bash "$CHECK" 2>&1)" || RC=$?
}

expect_fail() {
  local case_name="$1"; shift
  [[ "$RC" == 1 ]] || fail "$case_name: exit $RC, want 1: $OUT"
  for needle in "$@"; do
    grep -qF -- "$needle" <<<"$OUT" || fail "$case_name: output lacks '$needle': $OUT"
  done
  echo "ok: $case_name"
}

expect_pass() {
  [[ "$RC" == 0 ]] || fail "$1: exit $RC, want 0: $OUT"
  echo "ok: $1"
}

C_PTR='long long fx_get(long long x) {
  if (x) { return sprout_ptr_word(x); }
  return 0;
}'

fixture a1 stdlib/foo.sprout 'extern fn fx_vec(v: Vector Int) -> Int' ''
expect_fail "(A1) a vector-typed extern outside the allowlist" "fx_vec" "stdlib/foo.sprout"

fixture a2 stdlib/prelude.sprout 'extern fn vector_empty() -> Vector a' ''
expect_pass "(A2) an allowlisted vector extern"

fixture a3 stdlib/foo.sprout 'extern fn fx_a(v: VecLike) -> Int
extern fn fx_b(my_vec: Int) -> Int' ''
expect_pass "(A3) Vec inside a longer name is not a vector type"

fixture a4 ide/foo.sprout 'extern fn fx_vec(v: MutVec Int) -> Int' ''
expect_fail "(A4) ide/ is scanned" "fx_vec"

fixture b1 stdlib/foo.sprout 'extern fn fx_get(x: Int) -> Int' "$C_PTR"
expect_fail "(B1) -> Int returning sprout_ptr_word" "fx_get" "sprout_ptr_word"

fixture b2 stdlib/foo.sprout 'extern fn fx_get(x: Int) -> Int !{IO}' "$C_PTR"
expect_fail "(B2) -> Int !{IO} returning sprout_ptr_word" "fx_get"

fixture b3 examples/foo.sprout 'extern fn fx_get(x: Int) -> Bool' 'long long fx_get(long long x) { return sprout_str_word(x); }'
expect_fail "(B3) examples/ is scanned, sprout_str_word" "fx_get"

fixture b4 stdlib/foo.sprout 'extern fn fx_get(x: Int) -> Int' 'long long fx_get(long long x) {
  return x + 1;
}'
expect_pass "(B4) -> Int returning a plain integer"

fixture b5 stdlib/foo.sprout 'extern fn fx_get(x: Int) -> String' 'long long fx_get(long long x) { return sprout_str_word(x); }'
expect_pass "(B5) -> String returning sprout_str_word"

fixture b6 stdlib/foo.sprout 'extern fn fx_get(x: Int) -> Int' 'long long other(long long x) { return sprout_ptr_word(x); }
long long fx_get(long long x) { return 7; }'
expect_pass "(B6) only the named function body is read"

fixture b7 stdlib/foo.sprout 'extern fn fx_nodef(x: Int) -> Int' ''
expect_pass "(B7) no C definition is skipped"

fixture v1 stdlib/foo.sprout 'extern fn fx_nodef(x: Int) -> Int' ''
expect_pass "(V1) no-C-definition note is silent by default"
if grep -qF "no C definition" <<<"$OUT"; then fail "(V1) note printed without verbose: $OUT"; fi

VOUT="$(SPROUT_EXTERN_CHECK_VERBOSE=1 SPROUT_EXTERN_CHECK_ROOT="$TMPD/v1" bash "$CHECK" 2>&1)" || fail "(V2) verbose run failed: $VOUT"
grep -qF "no C definition: fx_nodef" <<<"$VOUT" || fail "(V2) verbose lacks note: $VOUT"
echo "ok: (V2) verbose lists no-C-definition externs"

fixture d1 stdlib/foo.sprout 'extern fn fx_dup(a: Int) -> Int
extern fn fx_dup(a: Int, b: Int) -> Int' ''
expect_fail "(D1) the duplicate rule still fires" "fx_dup" "declared more than once"

echo "==> test-check-extern-signatures ✓"
