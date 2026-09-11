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

# Per painted cell. Observed 15 objects / 20 swept on 2026-09-11.
#
# BOTH counters are budgeted, and gc_swept is the one that bites. Verified by
# building this probe against the pre-fast-path `grapheme`: it scored 15 objects
# — IDENTICAL — and 71 swept. `sprout_obj` does not count cstr allocations, and
# the bug was `str_slice` churn, so an objects-only budget would have passed the
# worst performance bug the TUI has had. Do not drop the swept budget as
# redundant; it is the only one that sees strings.
MAX_OBJ_PER_CELL=22
MAX_SWEPT_PER_CELL=30

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
obj=$(printf '%s\n' "$line" | sed -n 's/.*sprout_obj=\([0-9]*\).*/\1/p')
swept=$(printf '%s\n' "$line" | sed -n 's/.*gc_swept=\([0-9]*\).*/\1/p')

# A missing count means the report did not appear — the runtime lost
# SPROUT_DEBUG_ALLOC, or the probe stopped printing its denominator. Either way
# the gate is blind, and a blind gate must fail rather than pass.
if [ -z "$cells" ] || [ -z "$obj" ] || [ -z "$swept" ]; then
  echo "FAIL: could not read the counters (cells='$cells' obj='$obj' swept='$swept')" >&2
  echo "--- stdout ---" >&2; cat "$out" >&2
  echo "--- stderr ---" >&2; cat "$err" >&2
  exit 1
fi

obj_per=$(( (obj + cells - 1) / cells ))
swept_per=$(( (swept + cells - 1) / cells ))
echo "==> render cost: ${obj_per} objects and ${swept_per} swept per painted cell" \
     "(${obj} / ${swept} over ${cells} cells)"

fail=0
if [ "$obj_per" -gt "$MAX_OBJ_PER_CELL" ]; then
  echo "FAIL: $obj_per objects per cell exceeds the budget of $MAX_OBJ_PER_CELL" >&2
  fail=1
fi
if [ "$swept_per" -gt "$MAX_SWEPT_PER_CELL" ]; then
  echo "FAIL: $swept_per swept per cell exceeds the budget of $MAX_SWEPT_PER_CELL" >&2
  fail=1
fi
if [ "$fail" -ne 0 ]; then
  echo "      Rendering got more expensive. Find what each frame now allocates" >&2
  echo "      before raising the ceiling — that is the bug this gate is for." >&2
  exit 1
fi

echo "==> render-cost-gate: within budget"
