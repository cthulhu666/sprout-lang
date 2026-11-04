#!/usr/bin/env bash
# Specialisation gate (docs/specialisation-v0.md §7 M1, §8).
#
# Each fixture tests/spec_fixtures/<name>.spr is compiled twice, with the pass on
# (the default) and off (SPROUT_OPT_OFF=spec), and both builds are linked and run.
# Three things must hold:
#   - on and off print the same text (stdout + stderr) and exit with the same status;
#   - both equal the committed golden tests/spec_fixtures/expected/<name>.out;
#   - the IR assertions in the sidecar tests/spec_fixtures/<name>.ir hold.
# Each compile is bounded at 60 s, so a pass that never terminates fails instead of hanging.
#
# Sidecar format, one directive per line, fixed strings (grep -F), `#` starts a comment:
#   on|off has <text>             some line of that IR contains <text>
#   on|off lacks <text>           no line does
#   on|off count <n> <text>       exactly <n> lines do
#   on trace has <text>           some line of the pass-on compile's stderr (SPROUT_TRACE_SPEC=1)
#                                 contains <text>
#   on|off body <fn> has <text>   inside the `define` of the IR function <fn> (name without
#   on|off body <fn> lacks <text> the `@`), through its closing `}`. A missing define fails.
#   stress                        run the fixture under SPROUT_GC_STRESS=1
#
# Regenerate goldens (reads from the pass-OFF build): scripts/spec_gate.sh --snapshot
#
# Run: mise exec -- just spec-gate   (needs build/compile_driver_bin_stage1)
set -uo pipefail

STAGE1="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
FIX="tests/spec_fixtures"
GOLD="$FIX/expected"
TMP="$(mktemp -d /tmp/sprout_specgate_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
COMPILE_LIMIT=60
RUN_LIMIT=120
fail=0
snapshot=0
[ "${1:-}" = "--snapshot" ] && snapshot=1

if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
else
  CLANG_EXTRA=()
fi

source "$(dirname "${BASH_SOURCE[0]}")/golden_ir_lib.sh"
# shellcheck source=ir_body_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/ir_body_lib.sh"

build() { # <fixture> <on|off> -> $TMP/<fixture>.<mode>{,.ll}; returns 1 on failure, 2 on timeout
  local fx="$1" mode="$2" ll="$TMP/$1.$2.ll" ec
  if [ "$mode" = off ]; then
    SPROUT_OPT_OFF=spec limited "$COMPILE_LIMIT" "$STAGE1" --emit-ir stdlib "$FIX/$fx.spr" \
      > "$ll" 2> "$TMP/$fx.$mode.err"
  else
    SPROUT_TRACE_SPEC=1 limited "$COMPILE_LIMIT" "$STAGE1" --emit-ir stdlib "$FIX/$fx.spr" \
      > "$ll" 2> "$TMP/$fx.$mode.err"
  fi
  ec=$?
  [ "$ec" -eq 142 ] && return 2
  [ "$ec" -ne 0 ] && return 1
  clang "$ll" runtime/*.c -O2 "${CLANG_EXTRA[@]}" -o "$TMP/$fx.$mode" 2> "$TMP/$fx.$mode.link" || return 1
}

run_one() { # <stress 0|1> <binary> -> combined output plus an exit line
  local stress="$1" out ec; shift
  if [ "$stress" -eq 1 ]; then
    out="$(SPROUT_GC_STRESS=1 limited "$RUN_LIMIT" "$@" 2>&1)"; ec=$?
  else
    out="$(limited "$RUN_LIMIT" "$@" 2>&1)"; ec=$?
  fi
  printf '%s\nexit=%s\n' "$out" "$ec"
}

# <fixture> -> checks every directive of the sidecar against the IR
check_ir() {
  local fx="$1" side="$FIX/$1.ir" ll verb rest n fnname what text count body
  [ -f "$side" ] || { echo "  FAIL: $fx: no sidecar $side"; fail=1; return; }
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; stress) continue ;; esac
    read -r mode verb rest <<< "$line"
    if [ "$mode $verb" = "on trace" ]; then
      if grep -qF -- "${rest#has }" "$TMP/$fx.on.err"; then echo "  ok: $fx: on trace $rest"
      else echo "  FAIL: $fx: on trace missing: $rest"; fail=1; fi
      continue
    fi
    case "$mode" in on|off) ll="$TMP/$fx.$mode.ll" ;; *) echo "  FAIL: $fx: bad directive: $line"; fail=1; continue ;; esac
    case "$verb" in
      has)
        if grep -qF -- "$rest" "$ll"; then echo "  ok: $fx: $mode has $rest"
        else echo "  FAIL: $fx: $mode missing: $rest"; fail=1; fi ;;
      lacks)
        if grep -qF -- "$rest" "$ll"; then echo "  FAIL: $fx: $mode unwanted: $rest"; fail=1
        else echo "  ok: $fx: $mode lacks $rest"; fi ;;
      count)
        n="${rest%% *}"; text="${rest#* }"
        count="$(grep -cF -- "$text" "$ll")"
        if [ "$count" = "$n" ]; then echo "  ok: $fx: $mode count $n $text"
        else echo "  FAIL: $fx: $mode count: wanted $n, got $count: $text"; fail=1; fi ;;
      body)
        fnname="${rest%% *}"; rest="${rest#* }"
        what="${rest%% *}"; text="${rest#* }"
        body="$(ir_fn_body "$ll" "$fnname")"
        if [ -z "$body" ]; then echo "  FAIL: $fx: $mode: no define for @$fnname"; fail=1; continue; fi
        case "$what" in
          has)
            if printf '%s\n' "$body" | grep -qF -- "$text"; then echo "  ok: $fx: $mode body $fnname has $text"
            else echo "  FAIL: $fx: $mode body $fnname missing: $text"; fail=1; fi ;;
          lacks)
            if printf '%s\n' "$body" | grep -qF -- "$text"; then echo "  FAIL: $fx: $mode body $fnname unwanted: $text"; fail=1
            else echo "  ok: $fx: $mode body $fnname lacks $text"; fi ;;
          *) echo "  FAIL: $fx: bad body directive: $line"; fail=1 ;;
        esac ;;
      *) echo "  FAIL: $fx: bad directive: $line"; fail=1 ;;
    esac
  done < "$side"
}

mkdir -p "$GOLD"
for src in "$FIX"/*.spr; do
  fx="$(basename "$src" .spr)"
  stress=0
  grep -qx 'stress' "$FIX/$fx.ir" 2>/dev/null && stress=1

  built=1
  for mode in on off; do
    build "$fx" "$mode"; rc=$?
    if [ "$rc" -eq 2 ]; then
      echo "  FAIL: $fx: $mode build timed out after ${COMPILE_LIMIT}s"; fail=1; built=0
    elif [ "$rc" -ne 0 ]; then
      echo "  FAIL: $fx: $mode build failed"; head -5 "$TMP/$fx.$mode.err" "$TMP/$fx.$mode.link" 2>/dev/null; fail=1; built=0
    fi
  done
  [ "$built" -eq 1 ] || continue

  run_one "$stress" "$TMP/$fx.on" > "$TMP/$fx.on.out"
  run_one "$stress" "$TMP/$fx.off" > "$TMP/$fx.off.out"
  if [ "$snapshot" -eq 1 ]; then
    cp "$TMP/$fx.off.out" "$GOLD/$fx.out"; echo "  snapshot: $fx"; continue
  fi
  if ! cmp -s "$TMP/$fx.on.out" "$TMP/$fx.off.out"; then
    echo "  FAIL: $fx: pass on and off differ"
    diff "$TMP/$fx.off.out" "$TMP/$fx.on.out" | head -6; fail=1
  elif ! cmp -s "$TMP/$fx.on.out" "$GOLD/$fx.out"; then
    echo "  FAIL: $fx: output differs from $GOLD/$fx.out"
    diff "$GOLD/$fx.out" "$TMP/$fx.on.out" | head -6; fail=1
  else
    echo "  ok: $fx: output"
  fi
  check_ir "$fx"
done

if [ "$snapshot" -eq 1 ]; then echo "spec-gate: snapshot written"; exit 0; fi
if [ "$fail" -eq 0 ]; then echo "spec-gate: PASS"; else echo "spec-gate: FAIL"; fi
exit "$fail"
