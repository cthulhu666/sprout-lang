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
HUGE="$ROOT/tests/cost/rooting_block_huge.sprout"
# COUNTED from the fixtures, not declared here. Hardcoding them left the growth arm
# coupled to the files by nothing but a comment in them ("Change one, change all
# three"), and a truncated `huge` would have collapsed its delta to zero and read as
# perfectly flat — the vacuous green that arm exists to prevent, reintroduced by it.
fixture_n() { grep -c 'int_to_string([0-9]*) ++ "-row"' "$1"; }
SMALL_N=$(fixture_n "$SMALL")
LARGE_N=$(fixture_n "$LARGE")
HUGE_N=$(fixture_n "$HUGE")
DELTA_N=$((LARGE_N - SMALL_N))
DELTA2_N=$((HUGE_N - LARGE_N))

# The growth arm divides one per-element figure by the other and calls the result a
# ratio "at 2x block length", which is only true while the sizes really do double.
# Asserted rather than assumed, and before any compile, so a resized fixture fails
# here with its own message instead of somewhere downstream as a strange number.
if [ "$LARGE_N" -ne $((2 * SMALL_N)) ] || [ "$HUGE_N" -ne $((2 * LARGE_N)) ]; then
  echo "FAIL: the three cost fixtures must double: got $SMALL_N / $LARGE_N / $HUGE_N" >&2
  echo "      elements in rooting_block_{small,large,huge}.sprout." >&2
  echo "      The growth arm reports the ratio of two per-element figures as the cost" >&2
  echo "      of doubling the block; at other spacings that number means nothing, and" >&2
  echo "      a shrunken \`huge\` would read as perfectly flat. Resize all three." >&2
  exit 1
fi

# Everything above the literal must match too. `huge` once carried a three-line
# longer header, and header stripping is quadratic in header length, so the extra
# comment read as 901 arena bytes per element of "growth" (2026-10-04).
fixture_head() { sed '/^fn rows()/,$d' "$1"; }
if [ "$(fixture_head "$SMALL")" != "$(fixture_head "$LARGE")" ] \
   || [ "$(fixture_head "$SMALL")" != "$(fixture_head "$HUGE")" ]; then
  echo "FAIL: the cost fixtures' headers differ (everything above \`fn rows()\`)." >&2
  echo "      The gate reads differences between the fixtures as per-element cost, so" >&2
  echo "      a longer comment in one of them is measured as growth. Make them identical." >&2
  exit 1
fi

# Per element added to the literal. Observed 300 map / 7650 swept on 2026-09-28
# against a stage-2 build, with per-op live sets replaced by one per-block
# last-use index. Stage-1 scored 6752 swept on the same source — ~13%
# build-to-build spread, which is why nothing tight is budgeted on it.
#
# SWEPT has since fallen to 3259 (2026-09-29, stage-2), and by this gate's own
# subject rather than by drift: putting ast_to_ir's op accumulator on ListBuilder
# removed most of the objects, so most of the sweeping went with them. Read the
# floor against 3259, not 7650 — it is 2.2x below the observation now, not 5.1x.
# 3076 on 2026-09-30, after verify_dispatch's walk stopped copying its outcomes.
# 1913 on 2026-10-04, after the rooting scan stopped building a closure per
# comparison; the floor moved 1500 -> 1000 so the ~13% build spread fits under it.
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
MIN_SWEPT_PER_ELEM=1000

# ARENA BYTES, the only arm here that sees ir_lowering. Named for the
# allocator it covers: off-arena payloads are counted separately
# (offarena_bytes) and are NOT budgeted here. Observed 249472 per
# element on 2026-09-28 (stage-2); reintroducing right-nested `++` in lower_ops
# moved it to 1475716, a 5.9x separation that GROWS with block size. The same run
# moved sprout_obj by 0.8%, map by 0, gc_swept by -0.3% — so this arm, and only
# this arm, guards the string-building half.
# It is not ir_lowering's ALONE, though, which the 2026-09-28 note could not know:
# putting ast_to_ir's op accumulator on ListBuilder moved this arm 397637 -> 108996
# per element with lower_ops untouched. Objects and bytes both fall for an
# accumulator fix and only bytes fall for a string fix, so read the two together.
# Retightened to 250000 against the 108996 now observed; 550000 was set when a
# quadratic was already inside it.
MAX_ARENA_BYTES_PER_ELEM=250000
MIN_ARENA_BYTES_PER_ELEM=50000

# OBJECT COUNT. `map` prices the rooting pass and never moved for ast_to_ir's op
# accumulator (303 -> 343, flat); `arena_bytes` did move, so the accumulator was
# never invisible to this gate -- it was invisible to every CEILING, sitting at
# 397637 inside a budget of 550000. This arm is the one that measures it directly.
# It was quadratic in block length the whole time the gate was green: per-element
# sprout_obj measured 10454 / 19643 / 38002 at block sizes 120 / 240 / 480 --
# doubling with n, which is the signature, not the size.
#
# Observed 1251 per element on 2026-09-30 (stage-2): 1434 with the accumulator on
# ListBuilder, less verify_dispatch's walk no longer copying its outcome list. The
# back half is FLAT at 245 per element across those three sizes, and so is the
# front end now. A return of the quadratic overshoots this ceiling by 3.5x at THIS
# fixture size (10454 against 3000) and by more at any larger one -- ceiling-
# relative, the same convention as the map arm above.
MAX_SPROUT_OBJ_PER_ELEM=3000
MIN_SPROUT_OBJ_PER_ELEM=400

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just rooting-cost-gate" >&2
  exit 1
fi

err_small=$(mktemp /tmp/sprout_rooting_cost_s_XXXXXX)
err_large=$(mktemp /tmp/sprout_rooting_cost_l_XXXXXX)
err_huge=$(mktemp /tmp/sprout_rooting_cost_h_XXXXXX)
trap 'rm -f "$err_small" "$err_large" "$err_huge"' EXIT

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
  # The earliest failure point, so it dumps the compile's own output here. The
  # later empty-counter branch exists to do that and is never reached from here.
  echo "--- $1 ---" >&2; cat "$1" >&2
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
compile_one "$HUGE" "$err_huge"

for f in "$err_small" "$err_large" "$err_huge"; do
  require_unique "$f" map
  require_unique "$f" gc_swept
  require_unique "$f" arena_bytes
  require_unique "$f" sprout_obj
  require_unique "$f" closure
done

map_s=$(counter "$err_small" map);        map_l=$(counter "$err_large" map)
swept_s=$(counter "$err_small" gc_swept); swept_l=$(counter "$err_large" gc_swept)
sb_s=$(counter "$err_small" arena_bytes);  sb_l=$(counter "$err_large" arena_bytes)
obj_s=$(counter "$err_small" sprout_obj); obj_l=$(counter "$err_large" sprout_obj)
obj_h=$(counter "$err_huge" sprout_obj);  sb_h=$(counter "$err_huge" arena_bytes)
clo_s=$(counter "$err_small" closure); clo_l=$(counter "$err_large" closure)
clo_h=$(counter "$err_huge" closure)

# A missing counter means the report did not appear — the runtime lost
# SPROUT_DEBUG_ALLOC, or its format changed. A blind gate must fail, not pass.
if [ -z "$map_s" ] || [ -z "$map_l" ] || [ -z "$swept_s" ] || [ -z "$swept_l" ] \
   || [ -z "$sb_s" ] || [ -z "$sb_l" ] || [ -z "$obj_s" ] || [ -z "$obj_l" ] \
   || [ -z "$obj_h" ] || [ -z "$sb_h" ] || [ -z "$clo_s" ] || [ -z "$clo_l" ] \
   || [ -z "$clo_h" ]; then
  echo "FAIL: could not read the counters (map='$map_s'/'$map_l' swept='$swept_s'/'$swept_l'" >&2
  echo "      arena_bytes='$sb_s'/'$sb_l' sprout_obj='$obj_s'/'$obj_l'" >&2
  echo "      closure='$clo_s'/'$clo_l'/'$clo_h')" >&2
  echo "--- small ---" >&2; cat "$err_small" >&2
  echo "--- large ---" >&2; cat "$err_large" >&2
  exit 1
fi

# Signed, because a NEGATIVE delta is as broken as a tiny one: it means the
# larger fixture compiled less, so the pair is no longer a clean difference.
map_per=$(( (map_l - map_s) / DELTA_N ))
swept_per=$(( (swept_l - swept_s) / DELTA_N ))
sb_per=$(( (sb_l - sb_s) / DELTA_N ))
obj_per=$(( (obj_l - obj_s) / DELTA_N ))
echo "==> rooting cost: ${map_per} map, ${swept_per} swept, ${sb_per} arena bytes and ${obj_per} objects per element" \
     "($((map_l - map_s)) / $((swept_l - swept_s)) / $((sb_l - sb_s)) / $((obj_l - obj_s)) over $DELTA_N added elements)"

if [ "$map_per" -lt "$MIN_MAP_PER_ELEM" ] || [ "$swept_per" -lt "$MIN_SWEPT_PER_ELEM" ] \
   || [ "$sb_per" -lt "$MIN_ARENA_BYTES_PER_ELEM" ] || [ "$obj_per" -lt "$MIN_SPROUT_OBJ_PER_ELEM" ]; then
  echo "FAIL: $map_per map / $swept_per swept / $sb_per arena bytes / $obj_per objects per element is below the floor of" >&2
  echo "      $MIN_MAP_PER_ELEM / $MIN_SWEPT_PER_ELEM / $MIN_ARENA_BYTES_PER_ELEM / $MIN_SPROUT_OBJ_PER_ELEM. The two fixtures have stopped differing by" >&2
  echo "      $DELTA_N elements of one list literal, so the ceiling is guarding nothing." >&2
  echo "      Check what they contain before lowering the floor." >&2
  exit 1
fi

over=0
if [ "$map_per" -gt "$MAX_MAP_PER_ELEM" ]; then
  echo "FAIL: $map_per map allocations per element exceeds the budget of $MAX_MAP_PER_ELEM" >&2
  over=1
fi
if [ "$sb_per" -gt "$MAX_ARENA_BYTES_PER_ELEM" ]; then
  echo "FAIL: $sb_per arena bytes per element exceeds the budget of $MAX_ARENA_BYTES_PER_ELEM" >&2
  echo "      Two causes reach this arm, and the objects arm below tells them apart:" >&2
  echo "      if objects moved too, it is an op accumulator copying itself (see that" >&2
  echo "      arm). If objects held FLAT, it is string building before rooting: a" >&2
  echo "      right-nested \`++\` over one block's ops copies the whole remaining" >&2
  echo "      text per op. Collect parts and join once (see lower_ops_parts)." >&2
  over=1
fi
if [ "$obj_per" -gt "$MAX_SPROUT_OBJ_PER_ELEM" ]; then
  echo "FAIL: $obj_per objects per element exceeds the budget of $MAX_SPROUT_OBJ_PER_ELEM" >&2
  echo "      Counts, not bytes, and not the rooting pass: suspect an op accumulator" >&2
  echo "      appending to itself. \`list_append(ops, [op])\` per op copies the whole" >&2
  echo "      block; accumulate into a ListBuilder and build once where the block is" >&2
  echo "      sealed (see ast_to_ir's cur_ops)." >&2
  over=1
fi
# SHAPE, not level: the same per-element figure measured again over a block twice
# as long. Quadratic cost per element is proportional to block length, so it
# DOUBLES between the two measurements; linear cost holds flat. A ceiling cannot
# make this distinction, which is the whole reason a 550000-byte ceiling sat green
# over a per-element cost of 397637 that grew with every element added.
#
# Measured 2026-10-04 (stage-2): objects 100/100, bytes 102/100. Objects read
# 107/100 until the fixtures' headers were made identical: `huge` carried three
# more comment lines, and header stripping re-scanned from byte 0 per header line.
# That 7 was blamed on the fixtures' longer digits for a week; it was the header.
# Bytes read 112/100 until ir_lowering stopped appending each string global to the
# function's growing globals text, 130/100 until the rooting scan stopped building
# a closure per comparison (the closure arm below). Before verify_dispatch's walk
# stopped copying its outcome list the objects read 119/100; before ast_to_ir's op
# accumulator moved to ListBuilder, 180/100 on both.
#
# Hundredths, to stay in integer arithmetic: tenths floor 107 and 112 alike. The
# bounds sit a few points over what is measured, under every regression above.
# Re-measure before relaxing either.
MAX_OBJ_GROWTH_PCT=105
MAX_BYTES_GROWTH_PCT=106

obj_per2=$(( (obj_h - obj_l) / DELTA2_N ))
sb_per2=$(( (sb_h - sb_l) / DELTA2_N ))

# FLOOR THE SECOND DELTA, for the reason the first one is floored. A ratio is only
# as meaningful as its numerator: a `huge` fixture that compiles but does almost no
# extra work drives obj_per2 toward zero, and a ceiling-only arm reads that as
# "flat" and passes. Checked here, ahead of the ratio, so the failure names the
# fixtures rather than appearing as a suspiciously good number.
if [ "$obj_per2" -lt "$MIN_SPROUT_OBJ_PER_ELEM" ] || [ "$sb_per2" -lt "$MIN_ARENA_BYTES_PER_ELEM" ]; then
  echo "FAIL: over the SECOND delta ($LARGE_N -> $HUGE_N elements) the cost is" >&2
  echo "      $obj_per2 objects / $sb_per2 arena bytes per element, below the floor of" >&2
  echo "      $MIN_SPROUT_OBJ_PER_ELEM / $MIN_ARENA_BYTES_PER_ELEM. The huge fixture has stopped costing more than the large" >&2
  echo "      one, so the growth ratio below divides by a numerator that is not there" >&2
  echo "      and would read as perfectly flat. Check the fixture, not the budget." >&2
  exit 1
fi

obj_growth=$(( (100 * obj_per2) / obj_per ))
sb_growth=$(( (100 * sb_per2) / sb_per ))
echo "==> growth at 2x block length: objects ${obj_per} -> ${obj_per2} (${obj_growth}/100)," \
     "arena bytes ${sb_per} -> ${sb_per2} (${sb_growth}/100); flat is 100/100, quadratic is 200/100"

if [ "$obj_growth" -gt "$MAX_OBJ_GROWTH_PCT" ] || [ "$sb_growth" -gt "$MAX_BYTES_GROWTH_PCT" ]; then
  echo "FAIL: per-element cost GREW with block length (objects ${obj_growth}/100, bytes ${sb_growth}/100;" >&2
  echo "      budget ${MAX_OBJ_GROWTH_PCT}/100 and ${MAX_BYTES_GROWTH_PCT}/100). Cost per element that rises with the number of elements is" >&2
  echo "      quadratic in block length, whatever the absolute figures are -- the" >&2
  echo "      ceilings above can be green while this is broken, and have been." >&2
  echo "      Objects grew: an accumulator is being copied per element. Bytes only:" >&2
  echo "      a string is. Both: the same accumulator, since copying it costs both." >&2
  echo "      Split it by phase first: \`--phase bundle\`, \`--phase check\` and" >&2
  echo "      \`--phase recheck\` each stop earlier, and the phase whose own" >&2
  echo "      per-element cost rises is the one to read. The front end has done" >&2
  echo "      this too (verify_dispatch's outcome list), not only lowering." >&2
  over=1
fi

# CLOSURES, the rooting pass's own growth arm. Every `list_member` call builds its
# `Eq String` dictionary as a fresh closure, so a scan that compares strings and
# allocates nothing else still counts here. The pass once ran `list_member` against
# the root stack for every value in scope at every trigger: per-element closures
# measured 1212 -> 2382 (196/100) on 2026-10-04 (stage-2), while objects and map
# read flat and `--emit-ir` took 37 s on 960 elements. Scanning only the values
# defined since the last trigger: 36 -> 36 (100/100), and 0.34 s.
#
# BLIND SPOT: this sees the comparisons, not the walk. A rewrite that drops
# `list_member` but still visits every in-scope value per trigger is quadratic and
# allocates no closure; only time or a compiler-side counter would catch it. It also
# goes blind if dictionaries stop being built per call.
MAX_CLOSURE_GROWTH_PCT=125
# Floor at half the 36 observed, for the same reason as the floors above.
MIN_CLOSURE_PER_ELEM=18

clo_per=$(( (clo_l - clo_s) / DELTA_N ))
clo_per2=$(( (clo_h - clo_l) / DELTA2_N ))
if [ "$clo_per" -lt "$MIN_CLOSURE_PER_ELEM" ] || [ "$clo_per2" -lt "$MIN_CLOSURE_PER_ELEM" ]; then
  echo "FAIL: $clo_per / $clo_per2 closures per element is below the floor of $MIN_CLOSURE_PER_ELEM." >&2
  echo "      The ratio below would divide by nothing and read as flat. Check what the" >&2
  echo "      fixtures still allocate per element before lowering the floor." >&2
  exit 1
fi
clo_growth=$(( (100 * clo_per2) / clo_per ))
echo "==> closures at 2x block length: ${clo_per} -> ${clo_per2} (${clo_growth}/100)"
if [ "$clo_growth" -gt "$MAX_CLOSURE_GROWTH_PCT" ]; then
  echo "FAIL: closures per element grew ${clo_growth}/100 (budget ${MAX_CLOSURE_GROWTH_PCT}/100)." >&2
  echo "      Suspect a membership test against a list that grows with the block --" >&2
  echo "      in ir_rooting, the scan at each trigger. Only values defined since the" >&2
  echo "      last trigger can need a root; walk those, not the whole scope." >&2
  over=1
fi

if [ "$over" -ne 0 ]; then
  echo "      Per-op cost in a single block grew. If it now rises with block size," >&2
  echo "      compiling a generated vector suite will OOM rather than fail visibly." >&2
  echo "      Find what each op allocates before raising the ceiling." >&2
  exit 1
fi



echo "==> rooting-cost-gate: within budget"
