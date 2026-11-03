#!/usr/bin/env bash
# NOSCAN emission gate (docs/pointer-free-vectors-v0.md §4.2). A call to a creating extern
# (`vector_empty`, `vector_from_list`, `vec_make_filled`) whose result is `Vector T`, with T
# exactly Int, Bool, Char or Double, is followed by an inline write of the NOSCAN header bit.
# Any other result type keeps the bare call: a flag on a vector that can hold a heap pointer
# is a use-after-free that neither the mark nor GENCHECK can see, so the negative cases here
# matter more than the positive ones.
#
# Behaviour tests cannot catch a missing flag (the program prints the same either way), so
# this reads the emitted IR of each function in tests/noscan_fixtures/fixture_noscan.spr.
#
# Run: mise exec -- just noscan-gate   (needs build/compile_driver_bin_stage1)
set -uo pipefail

STAGE1="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
FIX="tests/noscan_fixtures/fixture_noscan.spr"
TMP="$(mktemp -d /tmp/sprout_noscangate_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail=0

# SYNC WITH SPROUT_VEC_NOSCAN_BIT in runtime/sprout_word.h: bit 63, as an LLVM i64 literal.
BIT='-9223372036854775808'

if ! "$STAGE1" --emit-ir stdlib "$FIX" > "$TMP/fixture.ll" 2>"$TMP/fixture.err"; then
  echo "  FAIL: the fixture does not compile: $(head -3 "$TMP/fixture.err")"
  echo "noscan-gate: FAIL"; exit 1
fi

# One function's body from emitted IR.
fn_body() { awk "/^define i64 @main\\.$1\\(/,/^\\}/" "$TMP/fixture.ll"; }

# Line numbers (within the body) of the creating call and of the flag `or`.
call_line() { grep -nE "call i64 @(vector_empty|vector_from_list|vec_make_filled)\\(" <<<"$1" | head -1 | cut -d: -f1; }
flag_line() { grep -nF -e "or i64" <<<"$1" | grep -F -e "$BIT" | head -1 | cut -d: -f1; }

for fn in from_list_int from_list_double from_list_char filled_int empty_bool from_list_then_length; do
  body="$(fn_body "$fn")"
  c="$(call_line "$body")"; f="$(flag_line "$body")"
  if [ -n "$c" ] && [ -n "$f" ] && [ "$f" -gt "$c" ]; then
    echo "  ok: $fn flags its result after the call"
  else
    echo "  FAIL: $fn has no NOSCAN write after its creating call"; fail=1
  fi
done

for fn in from_list_string filled_string empty_string from_list_record from_list_wrap \
          from_list_nested from_list_generic filled_generic empty_generic append_int; do
  body="$(fn_body "$fn")"
  if [ -n "$body" ] && ! grep -qF -e "$BIT" <<<"$body"; then
    echo "  ok: $fn is not flagged"
  else
    echo "  FAIL: $fn is flagged, or missing from the IR (a heap pointer may reach a NOSCAN vector)"; fail=1
  fi
done

# The IR with the writes in must still be valid LLVM.
if clang -c -x ir "$TMP/fixture.ll" -o "$TMP/fixture.o" 2>"$TMP/clang.err"; then
  echo "  ok: the flagged IR assembles"
else
  echo "  FAIL: the flagged IR does not assemble: $(head -3 "$TMP/clang.err")"; fail=1
fi

if [ "$fail" -eq 0 ]; then echo "noscan-gate: PASS"; else echo "noscan-gate: FAIL"; fi
exit "$fail"
