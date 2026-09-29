#!/usr/bin/env bash
# Gate: a frame's cost is BUDGETED, not benchmarked.
#
# Every other gate here asks whether the output is right. This one asks what it
# cost to produce, because the TUI's worst bug so far was correct output at ~100
# ms a frame: three UCD table searches per painted character, each slicing
# substrings per probe. Every test passed. Only a person using the app noticed.
#
# It counts ALLOCATIONS, not time. `SPROUT_DEBUG_ALLOC=1` makes the runtime
# report totals at exit, and for a workload with no clock and no input those
# totals repeat exactly — which is the property a wall-clock benchmark lacks and
# the reason `bench-string-concat` is deliberately not a gate (justfile §Bench).
#
# The budget is per painted cell and generous: it exists to catch the disaster
# (a 2x or 8x multiplier arriving unnoticed), not to police ordinary churn. The
# observed value is printed on every run, pass or fail, so drift is visible long
# before the ceiling is reached. A legitimate change that crosses it should move
# the ceiling in the same commit, with the new number in the message.
#
# Deliberately NOT an exact golden: the counts are stable on one machine but the
# gate must also pass on CI's Linux x86_64, and pinning exact values would trade
# a real signal for a maintenance tax.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_RENDER_COST_BIN:-$ROOT/build/render_cost}"

# Per painted cell. Observed 18 objects / 42 swept on 2026-09-11.
#
# BOTH counters are budgeted, and gc_swept is the one that bites. Verified by
# building this probe against the pre-fast-path `grapheme`: `sprout_obj` barely
# moved while swept multiplied. `sprout_obj` does not count cstr allocations,
# and that bug was `str_slice` churn, so an objects-only budget would have
# passed the worst performance bug the TUI has had. Do not drop the swept budget
# as redundant; it is the only one that sees strings.
MAX_OBJ_PER_CELL=26
MAX_SWEPT_PER_CELL=60

# A FLOOR, because "cheap" and "did nothing" are the same number to a budget. A
# layout regression that yields empty regions, or a list that renders no rows,
# would otherwise leave this gate green while measuring an empty screen — and
# nothing else in the repo exercises tests/cost/. Set near half the observed
# value: far below any real improvement, far above a collapse (a probe rendering
# two labels and nothing else scores 5 and 6).
MIN_OBJ_PER_CELL=9
MIN_SWEPT_PER_CELL=20

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just render-cost-gate" >&2
  exit 1
fi

out=$(mktemp /tmp/sprout_render_cost_XXXXXX)
err=$(mktemp /tmp/sprout_render_cost_err_XXXXXX)
trap 'rm -f "$out" "$err"' EXIT

SPROUT_DEBUG_ALLOC=1 "$BIN" > "$out" 2> "$err"
status=$?
if [ "$status" -ne 0 ]; then
  echo "FAIL: the probe exited $status" >&2
  cat "$err" >&2
  exit 1
fi

cells=$(sed -n 's/.*cost-probe cells=\([0-9]*\).*/\1/p' "$out")
line=$(grep '^\[sprout alloc\]' "$err" | tail -1)

# Anchored on the separating space, and required to be unique. `.*` is greedy, so
# an unanchored `sprout_obj=` binds to the LAST match — and the report is
# append-mostly, so a later counter whose name ends in one of these would be
# returned instead. That failure hands back a plausible NUMBER rather than
# nothing, so it sails past the empty-check below and the gate then budgets an
# unrelated counter. Same read as scripts/rooting_cost_gate.sh.
counter() { printf '%s\n' "$line" | sed -n "s/.*[[:space:]]$1=\([0-9]*\).*/\1/p"; }
# Every failure below dumps the probe's own output. This is the EARLIEST place
# the gate can fail, so without it a probe that dies before printing the report
# exits on a one-line "counter is GONE" and the crash that caused it is thrown
# away -- the later empty-counter branch, which exists to cat these, is never
# reached.
dump_probe() {
  echo "--- stdout ---" >&2; cat "$out" >&2
  echo "--- stderr ---" >&2; cat "$err" >&2
}
require_unique() {
  local n
  n=$(printf '%s\n' "$line" | grep -o "[[:space:]]$1=" | wc -l | tr -d ' ')
  [ "$n" -eq 1 ] && return 0
  echo "FAIL: '$1=' occurs $n time(s) in the alloc report; expected exactly 1." >&2
  if [ "$n" -eq 0 ]; then
    echo "      The counter is GONE, or the probe never printed the report at all." >&2
  else
    echo "      A counter whose name ends in '$1' was added to the report; this" >&2
    echo "      read is ambiguous, so it is not a budget." >&2
  fi
  dump_probe
  exit 1
}
require_unique sprout_obj
require_unique gc_swept
obj=$(counter sprout_obj)
swept=$(counter gc_swept)

# A missing count means the report did not appear — the runtime lost
# SPROUT_DEBUG_ALLOC, or the probe stopped printing its denominator. Either way
# the gate is blind, and a blind gate must fail rather than pass. Zero cells
# belongs here too: the division below would abort under `set -u` on the unset
# quotient, blaming this script for a probe edited down to no frames.
if [ -z "$cells" ] || [ "$cells" -eq 0 ] || [ -z "$obj" ] || [ -z "$swept" ]; then
  echo "FAIL: could not read the counters (cells='$cells' obj='$obj' swept='$swept')" >&2
  dump_probe
  exit 1
fi

obj_per=$(( (obj + cells - 1) / cells ))
swept_per=$(( (swept + cells - 1) / cells ))
echo "==> render cost: ${obj_per} objects and ${swept_per} swept per painted cell" \
     "(${obj} / ${swept} over ${cells} cells)"

over=0
under=0
if [ "$obj_per" -gt "$MAX_OBJ_PER_CELL" ]; then
  echo "FAIL: $obj_per objects per cell exceeds the budget of $MAX_OBJ_PER_CELL" >&2
  over=1
fi
if [ "$swept_per" -gt "$MAX_SWEPT_PER_CELL" ]; then
  echo "FAIL: $swept_per swept per cell exceeds the budget of $MAX_SWEPT_PER_CELL" >&2
  over=1
fi
if [ "$obj_per" -lt "$MIN_OBJ_PER_CELL" ] || [ "$swept_per" -lt "$MIN_SWEPT_PER_CELL" ]; then
  echo "FAIL: $obj_per objects / $swept_per swept per cell is below the floor of" >&2
  echo "      $MIN_OBJ_PER_CELL / $MIN_SWEPT_PER_CELL. The probe has stopped painting a full screen," >&2
  echo "      so the ceiling above is guarding nothing. Check what it renders" >&2
  echo "      before lowering the floor." >&2
  under=1
fi
if [ "$over" -ne 0 ]; then
  echo "      Rendering got more expensive. Find what each frame now allocates" >&2
  echo "      before raising the ceiling — that is the bug this gate is for." >&2
fi
if [ "$over" -ne 0 ] || [ "$under" -ne 0 ]; then
  exit 1
fi

echo "==> render-cost-gate: within budget"
