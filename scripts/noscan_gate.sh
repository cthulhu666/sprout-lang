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

# shellcheck source=ir_body_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/ir_body_lib.sh"

# One `main` function's body from the first fixture's IR.
fn_body() { ir_fn_body "$TMP/fixture.ll" "main.$1"; }

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

# ---------------------------------------------------------------------------
# Scalar copies (§4.3): a creating generic at Int or Double is called through its per-scalar
# copy (`vec_map$Int`), and the copy's own body carries the flag. Anything else keeps the
# original. Fixture 1 has no `main`, so dce keeps everything; fixture 2 has one, so the
# originals nothing reaches are dropped. Fixture 1 is also emitted with `specialise` off, where
# a constrained creator has no copy at all.
# ---------------------------------------------------------------------------
CFIX="tests/noscan_fixtures/fixture_noscan_copies.spr"
DFIX="tests/noscan_fixtures/fixture_noscan_dce.spr"
CLL="$TMP/copies.ll"
DLL="$TMP/dce.ll"

# The function a body's closure points at: `@__sprout_sclo.<symbol>,`.
closure_target() { grep -oE '@__sprout_sclo\.[^ ,)]+' <<<"$1" | head -1 | sed 's/^@__sprout_sclo\.//'; }

calls() { grep -qF -e "call i64 @$2(" <<<"$1"; }

# Whether a body reaches `$2`: a call, or a closure whose function calls it or is it.
reaches() {
  local target; target="$(closure_target "$1")"
  calls "$1" "$2" && return 0
  [ -n "$target" ] || return 1
  [ "$target" = "$2" ] && return 0
  calls "$(ir_fn_body "$CLL" "$target")" "$2"
}

# A function in the fixture calls the copy `$2` and no longer the original `$3`.
expect_copy() {
  local fn="$1" copy="$2" orig="$3" body
  body="$(ir_fn_body "$CLL" "main.$fn")"
  if [ -z "$body" ]; then echo "  FAIL: main.$fn is missing from the IR"; fail=1
  elif calls "$body" "$copy" && ! calls "$body" "$orig"; then echo "  ok: $fn calls $copy"
  else echo "  FAIL: $fn does not call the copy $copy"; fail=1; fi
}

# In the IR file `$1`, a function's body keeps the original `$3`, calls no `$Int`/`$Double`
# copy and has no flag.
expect_original() {
  local ll="$1" fn="$2" orig="$3" body
  body="$(ir_fn_body "$ll" "main.$fn")"
  if [ -z "$body" ]; then echo "  FAIL: main.$fn is missing from the IR"; fail=1
  elif calls "$body" "$orig" && ! grep -qE 'call i64 @[A-Za-z0-9_.]+\$(Int|Double)\(' <<<"$body" \
       && ! grep -qF -e "$BIT" <<<"$body"; then echo "  ok: $fn keeps $orig and is not flagged"
  else echo "  FAIL: $fn is flagged, or calls a copy, or lost $orig"; fail=1; fi
}

if ! "$STAGE1" --emit-ir stdlib "$CFIX" > "$CLL" 2>"$TMP/copies.err"; then
  echo "  FAIL: the copies fixture does not compile: $(head -3 "$TMP/copies.err")"; fail=1
else
  expect_copy map_int 'vec_map$Int' 'vec_map'
  expect_copy new_double 'stdlib.mutable.mutvec_new$Double' 'stdlib.mutable.mutvec_new'
  expect_copy empty_int 'stdlib.mutable.mutvec_empty$Int' 'stdlib.mutable.mutvec_empty'
  expect_copy from_list_int 'vec_from_list$Int' 'vec_from_list'
  expect_copy through_helper 'main.build_each$Int' 'main.build_each'

  # A value reference: the closure's function calls the copy.
  for fn in each_int 'build_each$Int'; do
    if reaches "$(ir_fn_body "$CLL" "main.$fn")" 'vec_from_list$Int'; then
      echo "  ok: $fn passes vec_from_list\$Int as a value"
    else
      echo "  FAIL: $fn does not reach the copy vec_from_list\$Int"; fail=1
    fi
  done

  # The copies' own bodies: a flag after the creating call, and the chain below vec_map.
  for sym in 'vec_from_list$Int' 'stdlib.mutable.mutvec_new$Double' 'stdlib.mutable.mutvec_empty$Int'; do
    body="$(ir_fn_body "$CLL" "$sym")"
    c="$(call_line "$body")"; f="$(flag_line "$body")"
    if [ -n "$c" ] && [ -n "$f" ] && [ "$f" -gt "$c" ]; then
      echo "  ok: $sym flags its result after the call"
    else
      echo "  FAIL: the copy $sym is missing, or has no NOSCAN write after its creating call"; fail=1
    fi
  done
  if calls "$(ir_fn_body "$CLL" 'vec_map$Int')" 'vec_map_indexed$Int' \
     && calls "$(ir_fn_body "$CLL" 'vec_map_indexed$Int')" 'vec_from_list$Int'; then
    echo "  ok: vec_map\$Int reaches vec_from_list\$Int through its helper's copy"
  else
    echo "  FAIL: vec_map\$Int -> vec_map_indexed\$Int -> vec_from_list\$Int is not a chain of copies"; fail=1
  fi

  # A builder behind a class dictionary: the instance method at Int has a concrete element
  # type, so it calls the flagged copy. `specialise` copies a constrained creator at Int and
  # that copy does too.
  for fn in '__tc_main.Single_Int_single' 'main.eq_vec$spec_Int'; do
    if calls "$(ir_fn_body "$CLL" "$fn")" 'vec_from_list$Int'; then
      echo "  ok: $fn calls vec_from_list\$Int"
    else
      echo "  FAIL: $fn is missing, or does not call the copy vec_from_list\$Int"; fail=1
    fi
  done

  # Negatives: a heap element, an unannotated helper.
  expect_original "$CLL" map_string 'vec_map'
  expect_original "$CLL" through_loose 'main.loose'
  if grep -qE '^define [^@]*@[^(]*\$String\(' "$CLL"; then
    echo "  FAIL: a \$String copy is defined (a heap element would be flagged)"; fail=1
  else
    echo "  ok: no \$String copy is defined"
  fi

  if clang -c -x ir "$CLL" -o "$TMP/copies.o" 2>"$TMP/clang.err"; then
    echo "  ok: the IR with copies assembles"
  else
    echo "  FAIL: the IR with copies does not assemble: $(head -3 "$TMP/clang.err")"; fail=1
  fi
fi

# A constrained creator that `specialise` does not copy stays scanned (§4.4): the same fixture
# with `specialise` off. `scalar_copies` copies only constraint-free functions, so a call
# through a dictionary keeps the original and is not flagged.
OLL="$TMP/spec_off.ll"
if ! SPROUT_OPT_OFF=spec "$STAGE1" --emit-ir stdlib "$CFIX" > "$OLL" 2>"$TMP/spec_off.err"; then
  echo "  FAIL: the copies fixture does not compile with specialise off: $(head -3 "$TMP/spec_off.err")"; fail=1
else
  expect_original "$OLL" through_eq_vec 'main.eq_vec'
  expect_original "$OLL" through_dict 'main.wrap_one'
  if grep -qE '^define [^@]*@main\.(eq_vec|wrap_one)\$' "$OLL"; then
    echo "  FAIL: a copy of main.eq_vec or main.wrap_one is defined with specialise off"; fail=1
  else
    echo "  ok: no copy of main.eq_vec or main.wrap_one is defined with specialise off"
  fi
fi

if ! "$STAGE1" --emit-ir stdlib "$DFIX" > "$DLL" 2>"$TMP/dce.err"; then
  echo "  FAIL: the dce fixture does not compile: $(head -3 "$TMP/dce.err")"; fail=1
else
  for pair in 'main.build:main.build$Int' 'vec_slice:vec_slice$Int'; do
    orig="${pair%%:*}"; copy="${pair##*:}"
    if [ -z "$(ir_fn_body "$DLL" "$copy")" ]; then
      echo "  FAIL: the copy $copy is not defined"; fail=1
    elif [ -n "$(ir_fn_body "$DLL" "$orig")" ]; then
      echo "  FAIL: $orig is still defined although only $copy is reached"; fail=1
    else
      echo "  ok: $copy is defined and $orig is dropped"
    fi
  done
fi

if [ "$fail" -eq 0 ]; then echo "noscan-gate: PASS"; else echo "noscan-gate: FAIL"; fi
exit "$fail"
