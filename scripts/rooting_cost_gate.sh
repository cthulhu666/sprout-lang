#!/usr/bin/env bash
# Gate: compiling one big basic block costs a BOUNDED amount per op.
#
# The rooting pass decides, at every op, which values must stay rooted across
# it. Asking that question per op with a materialised set per op is quadratic in
# block size, and nothing that checks output can see it: the IR was correct, the
# golden snapshots matched, every test passed. It surfaced as a generated vector
# suite taking 1.8 GB to compile and being SIGKILLed under CI's parallelism,
# which reads as `COMPILE FAILED` with empty stderr — a compile error, not a
# cost. That is the failure this gate exists to make loud — for the ROOTING pass
# only; see the gc_swept note below for the half it cannot see.
#
# It counts ALLOCATION COUNTS, not time or peak RSS. `SPROUT_DEBUG_ALLOC=1` makes the
# runtime report totals at exit, and compiling a fixed file repeats them
# exactly, which is what lets one number mean the same thing here and on CI's
# Linux x86_64. Peak RSS would not survive the trip.
#
# The measurement is a DIFFERENCE between two fixtures that differ only in
# element count, so the several hundred thousand allocations it takes to compile
# the prelude cancel and never enter the budget. A per-element number stays
# meaningful as the compiler grows; an absolute one would drift until it guarded
# nothing.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_ROOTING_COST_BIN:-$ROOT/build/compile_driver_bin_stage1}"
STDLIB="${SPROUT_ROOTING_COST_STDLIB:-$ROOT/stdlib}"
SMALL="$ROOT/tests/cost/rooting_block_small.sprout"
LARGE="$ROOT/tests/cost/rooting_block_large.sprout"
SMALL_N=60
LARGE_N=120
DELTA_N=$((LARGE_N - SMALL_N))

# Per element added to the literal. Observed 300 map / 7650 swept on 2026-09-28
# against a stage-2 build, with per-op live sets replaced by one per-block
# last-use index. Stage-1 scored 6752 swept on the same source — ~13%
# build-to-build spread, which is why nothing tight is budgeted on it.
#
# MAP is the sharp one: it was 4076 per element and rising with every element
# added, so a return of that quadratic overshoots this ceiling by ~6x at this
# fixture size and by more at any larger one.
MAX_MAP_PER_ELEM=650

# SWEPT IS FLOORED, NOT CAPPED, and the floor is a fixture check — NOT a churn
# detector. Measured by reintroducing right-nested `++` in ir_lowering.lower_ops
# and rebuilding: peak RSS went 88 MB -> 1907 MB on a 400-element literal, while
# gc_swept moved 7650 -> 7628 and map 300 -> 300. NO SIGNAL AT ALL, in either.
#
# An earlier revision of this comment claimed gc_swept separated the two by 1.7x.
# That was wrong, and the mistake is worth naming: the 11707 figure it compared
# against came from master's binary, which carried BOTH quadratics, so the
# movement was entirely the ROOTING fix. Measure the regression alone before
# claiming a counter guards it.
#
# The ir_lowering half is guarded by SLOT_BYTES, which the two concatenation
# forms DO separate: they allocate the same number of objects and differ only in
# bytes, so a count cannot see them and a byte total can. Right-nested `++`
# allocates a string as long as the whole remaining text at every step, so the
# per-element byte cost rises with block size where one-pass joining is flat.

# A FLOOR, for the reason render_cost_gate.sh documents: "cheap" and "compiled
# nothing" are the same number to a budget. If an edit leaves the two fixtures
# the same size, or the literal stops being one block, the delta collapses and
# the ceilings above guard nothing while staying green.
MIN_MAP_PER_ELEM=60
MIN_SWEPT_PER_ELEM=1500

# BYTES, which is the only arm here that sees ir_lowering. Observed 249472 per
# element on 2026-09-28 (stage-2); reintroducing right-nested `++` in lower_ops
# moved it to 1475716, a 5.9x separation that GROWS with block size. The same run
# moved sprout_obj by 0.8%, map by 0, gc_swept by -0.3% — so this arm, and only
# this arm, guards the string-building half.
MAX_SLOT_BYTES_PER_ELEM=550000
MIN_SLOT_BYTES_PER_ELEM=50000

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just rooting-cost-gate" >&2
  exit 1
fi

err_small=$(mktemp /tmp/sprout_rooting_cost_s_XXXXXX)
err_large=$(mktemp /tmp/sprout_rooting_cost_l_XXXXXX)
trap 'rm -f "$err_small" "$err_large"' EXIT

alloc_line() { grep '^\[sprout alloc\]' "$1" | tail -1; }

# Read a counter out of the runtime's exit report. Anchored on the separating
# space: `.*` is greedy, so an UNanchored `map=` binds to the last match, and the
# report is append-mostly — a later counter whose name ends in one we read
# (`slotmap=`, `bitmap=`) would be returned instead.
counter() { sed -n "s/.*[[:space:]]$2=\([0-9]*\).*/\1/p" <<< "$(alloc_line "$1")"; }

# The anchor alone is not enough, because the failure it prevents returns a
# plausible NUMBER rather than nothing — so it sails past the empty-check below,
# and the gate then budgets an unrelated counter and passes whatever the rooting
# pass costs. That is the exact vacuous green this gate exists to prevent, so the
# name is required to occur exactly once.
require_unique() {
  local n
  n=$(grep -o "[[:space:]]$2=" <<< "$(alloc_line "$1")" | wc -l | tr -d ' ')
  [ "$n" -eq 1 ] && return 0
  echo "FAIL: '$2=' occurs $n time(s) in the alloc report; expected exactly 1." >&2
  if [ "$n" -eq 0 ]; then
    echo "      The counter is GONE, so this arm is measuring nothing. Either the" >&2
    echo "      runtime stopped reporting it, or the report was not produced at all." >&2
  else
    echo "      A counter whose name ends in '$2' was probably added to the report." >&2
    echo "      This read is ambiguous, so it is not a budget — name the counters" >&2
    echo "      apart or anchor this read further." >&2
  fi
  exit 1
}

compile_one() {
  SPROUT_DEBUG_ALLOC=1 "$BIN" --emit-ir "$STDLIB" "$1" > /dev/null 2> "$2"
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "FAIL: compiling $1 exited $status" >&2
    cat "$2" >&2
    exit 1
  fi
}

compile_one "$SMALL" "$err_small"
compile_one "$LARGE" "$err_large"

for f in "$err_small" "$err_large"; do
  require_unique "$f" map
  require_unique "$f" gc_swept
  require_unique "$f" slot_bytes
done

map_s=$(counter "$err_small" map);        map_l=$(counter "$err_large" map)
swept_s=$(counter "$err_small" gc_swept); swept_l=$(counter "$err_large" gc_swept)
sb_s=$(counter "$err_small" slot_bytes);  sb_l=$(counter "$err_large" slot_bytes)

# A missing counter means the report did not appear — the runtime lost
# SPROUT_DEBUG_ALLOC, or its format changed. A blind gate must fail, not pass.
if [ -z "$map_s" ] || [ -z "$map_l" ] || [ -z "$swept_s" ] || [ -z "$swept_l" ] \
   || [ -z "$sb_s" ] || [ -z "$sb_l" ]; then
  echo "FAIL: could not read the counters (map='$map_s'/'$map_l' swept='$swept_s'/'$swept_l'" >&2
  echo "      slot_bytes='$sb_s'/'$sb_l')" >&2
  echo "--- small ---" >&2; cat "$err_small" >&2
  echo "--- large ---" >&2; cat "$err_large" >&2
  exit 1
fi

# Signed, because a NEGATIVE delta is as broken as a tiny one: it means the
# larger fixture compiled less, so the pair is no longer a clean difference.
map_per=$(( (map_l - map_s) / DELTA_N ))
swept_per=$(( (swept_l - swept_s) / DELTA_N ))
sb_per=$(( (sb_l - sb_s) / DELTA_N ))
echo "==> rooting cost: ${map_per} map, ${swept_per} swept and ${sb_per} slot bytes per element" \
     "($((map_l - map_s)) / $((swept_l - swept_s)) / $((sb_l - sb_s)) over $DELTA_N added elements)"

over=0
if [ "$map_per" -gt "$MAX_MAP_PER_ELEM" ]; then
  echo "FAIL: $map_per map allocations per element exceeds the budget of $MAX_MAP_PER_ELEM" >&2
  over=1
fi
if [ "$sb_per" -gt "$MAX_SLOT_BYTES_PER_ELEM" ]; then
  echo "FAIL: $sb_per slot bytes per element exceeds the budget of $MAX_SLOT_BYTES_PER_ELEM" >&2
  echo "      Bytes, not counts, so suspect string building before rooting: a" >&2
  echo "      right-nested \`++\` over one block's ops copies the whole remaining" >&2
  echo "      text per op. Collect parts and join once (see lower_ops_parts)." >&2
  over=1
fi
if [ "$over" -ne 0 ]; then
  echo "      Per-op cost in a single block grew. If it now rises with block size," >&2
  echo "      compiling a generated vector suite will OOM rather than fail visibly." >&2
  echo "      Find what each op allocates before raising the ceiling." >&2
  exit 1
fi

if [ "$map_per" -lt "$MIN_MAP_PER_ELEM" ] || [ "$swept_per" -lt "$MIN_SWEPT_PER_ELEM" ] \
   || [ "$sb_per" -lt "$MIN_SLOT_BYTES_PER_ELEM" ]; then
  echo "FAIL: $map_per map / $swept_per swept / $sb_per slot bytes per element is below the floor of" >&2
  echo "      $MIN_MAP_PER_ELEM / $MIN_SWEPT_PER_ELEM / $MIN_SLOT_BYTES_PER_ELEM. The two fixtures have stopped differing by" >&2
  echo "      $DELTA_N elements of one list literal, so the ceiling is guarding nothing." >&2
  echo "      Check what they contain before lowering the floor." >&2
  exit 1
fi

echo "==> rooting-cost-gate: within budget"
