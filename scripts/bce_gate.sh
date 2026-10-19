#!/usr/bin/env bash
# Bounds-check elimination gate (docs/bounds-check-elimination-v0.md).
#
# Each fixture in tests/bce_fixtures is compiled twice, with the pass on and with
# SPROUT_OPT_OFF=bce, and every case (argv[1]; none for a fixture without cases) is run
# against both. Three things must hold:
#   - the pass fires: the IR defines a `$fast` clone with it on, none with it off;
#   - on and off print the same text (stdout + stderr) and exit with the same status;
#   - both equal the committed golden in tests/bce_fixtures/expected/.
# A failing entry test must reach the original body, which panics where it always did.
#
# Regenerate goldens (reads from the pass-OFF build): scripts/bce_gate.sh --snapshot
#
# Run: mise exec -- just bce-gate   (needs build/compile_driver_bin_stage1)
set -uo pipefail

STAGE1="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
FIX="tests/bce_fixtures"
GOLD="$FIX/expected"
TMP="$(mktemp -d /tmp/sprout_bcegate_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
fail=0
snapshot=0
[ "${1:-}" = "--snapshot" ] && snapshot=1

if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
else
  CLANG_EXTRA=()
fi

# fixture:case,case,... ("" cases = run once with no argument)
CASES=(
  "copy_cases:ok,stop_past_end,start_negative,start_min,dst_short,dst_start_negative,stop_max,dst_max,empty_range,down_ok,down_n_big,down_start_negative,rev_ok,rev_k_low,rev_k_high"
  "insert_cases:ok,lo_negative,lo_min,j_at_len,j_past_len,j_negative,j_max,hi_past_len,lo_past_j"
  "generic_sort:"
)

build() { # <fixture> <on|off> -> $TMP/<fixture>.<mode>
  local fx="$1" mode="$2" ll="$TMP/$1.$2.ll"
  if [ "$mode" = off ]; then
    SPROUT_OPT_OFF=bce "$STAGE1" --emit-ir stdlib "$FIX/$fx.spr" > "$ll" 2> "$TMP/$fx.$mode.err" || return 1
  else
    "$STAGE1" --emit-ir stdlib "$FIX/$fx.spr" > "$ll" 2> "$TMP/$fx.$mode.err" || return 1
  fi
  clang "$ll" runtime/*.c -O2 "${CLANG_EXTRA[@]}" -o "$TMP/$fx.$mode" 2> "$TMP/$fx.$mode.link" || return 1
}

run_one() { # <binary> [case] -> combined output plus an exit line
  local out ec
  out="$("$@" 2>&1)"; ec=$?
  printf '%s\nexit=%s\n' "$out" "$ec"
}

mkdir -p "$GOLD"
for entry in "${CASES[@]}"; do
  fx="${entry%%:*}"
  cases="${entry#*:}"
  if ! build "$fx" on || ! build "$fx" off; then
    echo "  FAIL: $fx: could not build"; head -5 "$TMP/$fx".*.err "$TMP/$fx".*.link 2>/dev/null; fail=1; continue
  fi
  if grep -qF '$fast(' "$TMP/$fx.on.ll"; then
    echo "  ok: $fx: pass fires"
  else
    echo "  FAIL: $fx: no \$fast clone with the pass on"; fail=1
  fi
  if grep -qF '$fast(' "$TMP/$fx.off.ll"; then
    echo "  FAIL: $fx: SPROUT_OPT_OFF=bce still emits a \$fast clone"; fail=1
  fi
  IFS=',' read -r -a list <<< "$cases"
  [ "${#list[@]}" -eq 0 ] && list=("")
  for c in "${list[@]}"; do
    name="$fx.${c:-run}"
    if [ -n "$c" ]; then
      run_one "$TMP/$fx.on" "$c" > "$TMP/$name.on.out"
      run_one "$TMP/$fx.off" "$c" > "$TMP/$name.off.out"
    else
      run_one "$TMP/$fx.on" > "$TMP/$name.on.out"
      run_one "$TMP/$fx.off" > "$TMP/$name.off.out"
    fi
    if [ "$snapshot" -eq 1 ]; then
      cp "$TMP/$name.off.out" "$GOLD/$name.out"; echo "  snapshot: $name"; continue
    fi
    if ! cmp -s "$TMP/$name.on.out" "$TMP/$name.off.out"; then
      echo "  FAIL: $name: pass on and off differ"
      diff "$TMP/$name.off.out" "$TMP/$name.on.out" | head -6; fail=1
    elif ! cmp -s "$TMP/$name.on.out" "$GOLD/$name.out"; then
      echo "  FAIL: $name: output differs from tests/bce_fixtures/expected/$name.out"
      diff "$GOLD/$name.out" "$TMP/$name.on.out" | head -6; fail=1
    else
      echo "  ok: $name"
    fi
  done
done

if [ "$fail" -eq 0 ]; then echo "bce-gate: PASS"; else echo "bce-gate: FAIL"; fi
exit "$fail"
