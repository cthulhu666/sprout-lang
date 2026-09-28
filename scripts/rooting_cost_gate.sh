#!/usr/bin/env bash
# Gate: compiling one big basic block costs a BOUNDED amount per op.
#
# The rooting pass decides, at every op, which values must stay rooted across
# it. Asking that question per op with a materialised set per op is quadratic in
# block size, and nothing that checks output can see it: the IR was correct, the
# golden snapshots matched, every test passed. It surfaced as a generated vector
# suite taking 1.8 GB to compile and being SIGKILLed under CI's parallelism,
# which reads as `COMPILE FAILED` with empty stderr — a compile error, not a
# cost. That is the failure this gate exists to make loud.
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

# Per element added to the literal. Observed 301 map / 6752 swept on 2026-09-28,
# with per-op live sets replaced by one per-block last-use index and IR text
# rendered through string_concat_many.
#
# MAP is the sharp one: it was 4076 per element and rising with every element
# added, so a return of that quadratic overshoots this ceiling by ~6x at this
# fixture size and by more at any larger one.
MAX_MAP_PER_ELEM=650

# SWEPT IS DELIBERATELY NOT CAPPED, only floored. It is the counter that best
# sees string churn (sprout_obj ignores cstr allocations, per docs/gates.md), so
# a ceiling here is tempting — and it would be dishonest. Right-nested `++`
# scored 11707 per element against 6752..7650 for the fixed compiler, where that
# spread is two builds of the SAME source: ~13% build-to-build noise against a
# 1.5x separation from the bug. A budget inside that noise either flakes or
# fails to fire.
#
# So the string half of this file's subject is UNGUARDED, and bytes are the
# reason: the two concatenation forms allocate the same NUMBER of objects and
# differ only in bytes copied, which no counter here reports. A 22x peak-RSS
# regression moves gc_swept 1.7x and sprout_obj 16%. This is a cost gate, not a
# memory gate; closing that needs a bytes counter or a portable peak-RSS arm.

# A FLOOR, for the reason render_cost_gate.sh documents: "cheap" and "compiled
# nothing" are the same number to a budget. If an edit leaves the two fixtures
# the same size, or the literal stops being one block, the delta collapses and
# the ceilings above guard nothing while staying green.
MIN_MAP_PER_ELEM=60
MIN_SWEPT_PER_ELEM=1500

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just rooting-cost-gate" >&2
  exit 1
fi

err_small=$(mktemp /tmp/sprout_rooting_cost_s_XXXXXX)
err_large=$(mktemp /tmp/sprout_rooting_cost_l_XXXXXX)
trap 'rm -f "$err_small" "$err_large"' EXIT

# Read a counter out of the runtime's exit report.
counter() { sed -n "s/.*$2=\([0-9]*\).*/\1/p" <<< "$(grep '^\[sprout alloc\]' "$1" | tail -1)"; }

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

map_s=$(counter "$err_small" map);        map_l=$(counter "$err_large" map)
swept_s=$(counter "$err_small" gc_swept); swept_l=$(counter "$err_large" gc_swept)

# A missing counter means the report did not appear — the runtime lost
# SPROUT_DEBUG_ALLOC, or its format changed. A blind gate must fail, not pass.
if [ -z "$map_s" ] || [ -z "$map_l" ] || [ -z "$swept_s" ] || [ -z "$swept_l" ]; then
  echo "FAIL: could not read the counters (map='$map_s'/'$map_l' swept='$swept_s'/'$swept_l')" >&2
  echo "--- small ---" >&2; cat "$err_small" >&2
  echo "--- large ---" >&2; cat "$err_large" >&2
  exit 1
fi

# Signed, because a NEGATIVE delta is as broken as a tiny one: it means the
# larger fixture compiled less, so the pair is no longer a clean difference.
map_per=$(( (map_l - map_s) / DELTA_N ))
swept_per=$(( (swept_l - swept_s) / DELTA_N ))
echo "==> rooting cost: ${map_per} map and ${swept_per} swept per element" \
     "($((map_l - map_s)) / $((swept_l - swept_s)) over $DELTA_N added elements)"

over=0
if [ "$map_per" -gt "$MAX_MAP_PER_ELEM" ]; then
  echo "FAIL: $map_per map allocations per element exceeds the budget of $MAX_MAP_PER_ELEM" >&2
  over=1
fi
if [ "$over" -ne 0 ]; then
  echo "      Per-op cost in a single block grew. If it now rises with block size," >&2
  echo "      compiling a generated vector suite will OOM rather than fail visibly." >&2
  echo "      Find what each op allocates before raising the ceiling." >&2
  exit 1
fi

if [ "$map_per" -lt "$MIN_MAP_PER_ELEM" ] || [ "$swept_per" -lt "$MIN_SWEPT_PER_ELEM" ]; then
  echo "FAIL: $map_per map / $swept_per swept per element is below the floor of" >&2
  echo "      $MIN_MAP_PER_ELEM / $MIN_SWEPT_PER_ELEM. The two fixtures have stopped differing by" >&2
  echo "      $DELTA_N elements of one list literal, so the ceiling is guarding nothing." >&2
  echo "      Check what they contain before lowering the floor." >&2
  exit 1
fi

echo "==> rooting-cost-gate: within budget"
