#!/usr/bin/env bash
# Optimisation-pass A/B harness (docs/opt-passes-v0.md §M0).
#
# One compiler build. Each program is compiled twice — every pass ON, then the
# pass under test OFF via SPROUT_OPT_OFF — so an A/B costs two compiles instead
# of two bootstraps, which is what lets it run in CI at all.
#
# Usage: bench/optpasses/bench.sh [pass]        (default: dle)
set -uo pipefail

PASS="${1:-dle}"
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
CC="$REPO/build/compile_driver_bin_stage1"
RUNS="${RUNS:-4}"

cd "$REPO"

if [[ ! -x "$CC" ]]; then
  echo "ERROR: $CC not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

CLANG_EXTRA=()
[[ "$(uname -s)" == "Darwin" ]] && CLANG_EXTRA=(-framework Security -framework CoreFoundation)

TMPD=$(mktemp -d /tmp/sprout_optbench_XXXXXX)
trap 'rm -rf "$TMPD"' EXIT

# label | source | run?  — `no` means compile-time only, for programs that need
# arguments or are too slow to run warm.
CORPUS=(
  "astar|examples/astar.sprout|yes"
  "nqueens|examples/nqueens.sprout|yes"
  "math_transcendental|bench/math_transcendental/math_transcendental_bench.sprout|yes"
  "unboxed_read|bench/unboxed_read/unboxed_read_bench.sprout|yes"
  "digit_recognizer|examples/digit_recognizer/recognizer.sprout|yes"
  "compile_driver|stdlib/compiler/compile_driver.sprout|no"
)

# Minimum wall-clock over $RUNS attempts. The minimum estimates the cost; the
# mean estimates the background load (bench/results-2026-08-06 §"On using the
# minimum").
min_elapsed() {
  local best="" out t i
  for ((i = 0; i < RUNS; i++)); do
    out=$( { /usr/bin/time -p "$@" >/dev/null; } 2>&1 )
    t=$(awk '/^real/{print $2}' <<< "$out")
    [[ -z "$t" ]] && continue
    if [[ -z "$best" ]] || awk -v a="$t" -v b="$best" 'BEGIN{exit !(a < b)}'; then best="$t"; fi
  done
  echo "${best:-n/a}"
}

emit() {  # emit <src> <out.ll> <err> [pass-to-disable]
  local src="$1" out="$2" err="$3" off="${4:-}"
  if [[ -n "$off" ]]; then
    SPROUT_OPT_STATS=1 SPROUT_OPT_OFF="$off" "$CC" --emit-ir stdlib "$src" > "$out" 2> "$err"
  else
    SPROUT_OPT_STATS=1 "$CC" --emit-ir stdlib "$src" > "$out" 2> "$err"
  fi
}

stat_field() {  # stat_field <err-file> <key>
  awk -v k="$2" '/^\[opt\]/ { for (i = 1; i <= NF; i++) if ($i ~ "^" k "=") { sub("^" k "=", "", $i); print $i } }' "$1"
}

echo "==> Pass A/B: $PASS  (compiler: $CC, $RUNS runs per timing, minimum reported)"
echo
printf '%-22s %9s %8s %9s %9s %8s %8s %8s %8s\n' \
  program nodes removed "IR-on" "IR-off" "cc-on" "cc-off" "run-on" "run-off"
printf '%-22s %9s %8s %9s %9s %8s %8s %8s %8s\n' \
  "----------------------" "---------" "--------" "---------" "---------" "--------" "--------" "--------" "--------"

fired=0
for entry in "${CORPUS[@]}"; do
  IFS='|' read -r label src runnable <<< "$entry"
  if [[ ! -f "$src" ]]; then
    printf '%-22s %10s\n' "$label" "MISSING"
    continue
  fi

  on_ll="$TMPD/$label.on.ll"; off_ll="$TMPD/$label.off.ll"
  emit "$src" "$on_ll" "$TMPD/$label.on.err" || { printf '%-22s %10s\n' "$label" "EMIT-FAIL"; continue; }
  emit "$src" "$off_ll" "$TMPD/$label.off.err" "$PASS" || { printf '%-22s %10s\n' "$label" "EMIT-FAIL"; continue; }

  nodes=$(stat_field "$TMPD/$label.on.err" nodes)
  removed=$(stat_field "$TMPD/$label.on.err" removed)
  [[ "${removed:-0}" != "0" ]] && fired=$((fired + 1))

  on_lines=$(wc -l < "$on_ll" | tr -d ' ')
  off_lines=$(wc -l < "$off_ll" | tr -d ' ')

  # Compile time: the pass's own cost, which is the half a runtime benchmark
  # cannot see when the pass turns out to change nothing.
  cc_on=$(min_elapsed "$CC" --emit-ir stdlib "$src")
  cc_off=$(min_elapsed env "SPROUT_OPT_OFF=$PASS" "$CC" --emit-ir stdlib "$src")

  on_t="-"; off_t="-"
  if [[ "$runnable" == "yes" ]]; then
    if clang "$on_ll" runtime/*.c -O2 "${CLANG_EXTRA[@]}" -o "$TMPD/$label.on" 2>/dev/null \
       && clang "$off_ll" runtime/*.c -O2 "${CLANG_EXTRA[@]}" -o "$TMPD/$label.off" 2>/dev/null; then
      on_t=$(min_elapsed "$TMPD/$label.on")
      off_t=$(min_elapsed "$TMPD/$label.off")
    else
      on_t="LINK-FAIL"; off_t="LINK-FAIL"
    fi
  fi

  printf '%-22s %9s %8s %9s %9s %8s %8s %8s %8s\n' \
    "$label" "${nodes:-?}" "${removed:-?}" "$on_lines" "$off_lines" "$cc_on" "$cc_off" "$on_t" "$off_t"
done

echo
if (( fired == 0 )); then
  echo "NOTE: $PASS removed nothing anywhere in this corpus — the ON and OFF columns"
  echo "      are expected to match. That is a result about the pass, not a broken harness;"
  echo "      tests/opt_harness/dead_let.spr is the fixture where it does fire."
fi
