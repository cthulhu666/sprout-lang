#!/usr/bin/env bash
# Gate: compiling one more top-level function costs a BOUNDED amount.
#
# Mutual TCO's Phase B finds tail-call cycles over every function in the program.
# It once asked "does f reach g, and g reach f?" for every pair, one graph search
# per question, which is cubic in the length of a tail-call chain: 400 chained
# functions took 48 s and 33 GB of arena to compile (2026-10-04, stage-2), and the
# same pass was 55% of the compiler's own self-compile allocation. Nothing that
# checks output saw it, because the IR was right.
#
# Each link also passes a lambda. Inference once turned the whole type environment
# into a list for every lambda, twice, to pick out its alias markers
# (infer.aliases_for_annotations): 18% of the self-compile's allocation, and a
# cost that grows with the environment.
#
# The fixtures are a tail-call chain at three doubling lengths. The gate counts
# ALLOCATIONS (`SPROUT_DEBUG_ALLOC=1`), which repeat exactly from run to run and
# across platforms, and reads DIFFERENCES between fixtures, so compiling the
# prelude cancels out. rooting_cost_gate.sh, whose fixtures are one long basic
# block, cannot see this: its function count does not vary.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_SCC_COST_BIN:-$ROOT/build/compile_driver_bin_stage1}"
STDLIB="${SPROUT_SCC_COST_STDLIB:-$ROOT/stdlib}"
SMALL="$ROOT/tests/cost/scc_chain_small.sprout"
LARGE="$ROOT/tests/cost/scc_chain_large.sprout"
HUGE="$ROOT/tests/cost/scc_chain_huge.sprout"

# Counted from the fixtures, so a truncated one fails here rather than reading as flat.
fixture_n() { grep -c '^fn link_' "$1"; }
SMALL_N=$(fixture_n "$SMALL")
LARGE_N=$(fixture_n "$LARGE")
HUGE_N=$(fixture_n "$HUGE")
DELTA_N=$((LARGE_N - SMALL_N))
DELTA2_N=$((HUGE_N - LARGE_N))
if [ "$LARGE_N" -ne $((2 * SMALL_N)) ] || [ "$HUGE_N" -ne $((2 * LARGE_N)) ]; then
  echo "FAIL: the SCC cost fixtures must double: got $SMALL_N / $LARGE_N / $HUGE_N links." >&2
  echo "      The growth arm reads its ratio as the cost of doubling the chain." >&2
  exit 1
fi

# Everything above the chain must match too: a longer comment in one fixture is
# measured as per-link cost (rooting_cost_gate.sh found that out the hard way).
fixture_head() { sed '/^fn link_0(/,$d' "$1"; }
if [ "$(fixture_head "$SMALL")" != "$(fixture_head "$LARGE")" ] \
   || [ "$(fixture_head "$SMALL")" != "$(fixture_head "$HUGE")" ]; then
  echo "FAIL: the SCC cost fixtures' headers differ (everything above \`fn link_0\`)." >&2
  exit 1
fi

# Per link, 2026-10-05 (stage-2), first delta: 5404 objects and 257959 arena bytes.
# The whole-environment walk per lambda read 21464 and 797229 (1.9x and 1.5x these
# ceilings); pairwise SCC reachability overshot them 7x and 19x before that, on
# fixtures without the lambda.
MAX_SPROUT_OBJ_PER_LINK=11000
MAX_ARENA_BYTES_PER_LINK=520000
# Floors at half the observation: "cheap" and "compiled nothing" are the same number
# to a ceiling.
MIN_SPROUT_OBJ_PER_LINK=2700
MIN_ARENA_BYTES_PER_LINK=129000
# Hundredths. Now: objects 100/100, bytes 101/100. The environment walk: 109/100 and
# 108/100, which is why the ceilings and not this arm caught it. Pairwise SCC
# reachability: 231/100 and 264/100.
MAX_GROWTH_PCT=110

if [ ! -x "$BIN" ]; then
  echo "ERROR: $BIN not found; run: just scc-cost-gate" >&2
  exit 1
fi

err_small=$(mktemp /tmp/sprout_scc_cost_s_XXXXXX)
err_large=$(mktemp /tmp/sprout_scc_cost_l_XXXXXX)
err_huge=$(mktemp /tmp/sprout_scc_cost_h_XXXXXX)
trap 'rm -f "$err_small" "$err_large" "$err_huge"' EXIT

alloc_line() { grep '^\[sprout alloc\]' "$1" | tail -1; }
# Anchored on the separating space; see rooting_cost_gate.sh for why.
counter() { sed -n "s/.*[[:space:]]$2=\([0-9]*\).*/\1/p" <<< "$(alloc_line "$1")"; }
require_unique() {
  local n
  n=$(grep -o "[[:space:]]$2=" <<< "$(alloc_line "$1")" | wc -l | tr -d ' ')
  [ "$n" -eq 1 ] && return 0
  echo "FAIL: '$2=' occurs $n time(s) in the alloc report; expected exactly 1." >&2
  echo "--- $1 ---" >&2; cat "$1" >&2
  exit 1
}

# A fixture that fails to compile still prints an alloc report, of a compile that
# stopped early. The exit status is what tells the two apart.
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
  require_unique "$f" sprout_obj
  require_unique "$f" arena_bytes
done

obj_s=$(counter "$err_small" sprout_obj); obj_l=$(counter "$err_large" sprout_obj)
obj_h=$(counter "$err_huge" sprout_obj)
sb_s=$(counter "$err_small" arena_bytes);  sb_l=$(counter "$err_large" arena_bytes)
sb_h=$(counter "$err_huge" arena_bytes)

obj_per=$(( (obj_l - obj_s) / DELTA_N ));  obj_per2=$(( (obj_h - obj_l) / DELTA2_N ))
sb_per=$(( (sb_l - sb_s) / DELTA_N ));     sb_per2=$(( (sb_h - sb_l) / DELTA2_N ))
echo "==> scc cost: ${obj_per} objects and ${sb_per} arena bytes per link" \
     "($SMALL_N -> $LARGE_N links); ${obj_per2} / ${sb_per2} ($LARGE_N -> $HUGE_N)"

# Floors on BOTH deltas: a fixture that stops costing more than the smaller one
# drives the ratio below toward zero, which reads as flat.
for v in "$obj_per:$MIN_SPROUT_OBJ_PER_LINK:objects" "$obj_per2:$MIN_SPROUT_OBJ_PER_LINK:objects" \
         "$sb_per:$MIN_ARENA_BYTES_PER_LINK:arena bytes" "$sb_per2:$MIN_ARENA_BYTES_PER_LINK:arena bytes"; do
  IFS=: read -r got floor what <<< "$v"
  if [ "$got" -lt "$floor" ]; then
    echo "FAIL: $got $what per link is below the floor of $floor. The fixtures have" >&2
    echo "      stopped differing by a chain of functions, so the bounds guard nothing." >&2
    echo "      Check what they contain before lowering the floor." >&2
    exit 1
  fi
done

over=0
if [ "$obj_per" -gt "$MAX_SPROUT_OBJ_PER_LINK" ] || [ "$sb_per" -gt "$MAX_ARENA_BYTES_PER_LINK" ]; then
  echo "FAIL: $obj_per objects / $sb_per arena bytes per link exceeds the budget of" >&2
  echo "      $MAX_SPROUT_OBJ_PER_LINK / $MAX_ARENA_BYTES_PER_LINK. Suspect work per function or per" >&2
  echo "      lambda that reads the whole type environment (dict_entries(env))." >&2
  over=1
fi

# SHAPE, not level: per-link cost measured again over a chain twice as long.
obj_growth=$(( (100 * obj_per2) / obj_per ))
sb_growth=$(( (100 * sb_per2) / sb_per ))
echo "==> growth at 2x chain length: objects ${obj_growth}/100, arena bytes ${sb_growth}/100;" \
     "flat is 100/100, quadratic 200/100"
if [ "$obj_growth" -gt "$MAX_GROWTH_PCT" ] || [ "$sb_growth" -gt "$MAX_GROWTH_PCT" ]; then
  echo "FAIL: per-link cost GREW with chain length (objects ${obj_growth}/100, bytes" >&2
  echo "      ${sb_growth}/100; budget ${MAX_GROWTH_PCT}/100), so compile cost is superlinear in" >&2
  echo "      the number of functions. Suspect a whole-program pass that searches the" >&2
  echo "      call graph per function or per pair: Phase B's SCC pass did" >&2
  echo "      (ast_to_ir.pb_hetero_sccs), and Phase A's mutual_reaches per same-arity" >&2
  echo "      tail edge still does, which these fixtures dodge by alternating arity." >&2
  over=1
fi

if [ "$over" -ne 0 ]; then exit 1; fi
echo "==> scc-cost-gate: within budget"
