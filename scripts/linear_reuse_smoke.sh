#!/usr/bin/env bash
# Gate: in-place reuse of a consumed linear cell (docs/linear-reuse-v0.md). `just test` counts
# `reuse_ctor` in printed IR; this checks what that does at run time, at -O2.
#
#   alloc   a linear state stepped once per input unit allocates nothing per step: the
#           runtime's SPROUT_DEBUG_ALLOC object count is the same for a small and a large
#           input, for the Grid sieve (one rebuild per strike, same constructor), the CSV
#           splitter (a rebuild per byte, three constructors) and a binary search tree
#           (an insert rebuilds a whole path; none allocates once every key is present).
#   count   a fixture run at two sizes differs in one runtime counter by an expected amount,
#           for what the API hides: the `vector` counter counts element buffers allocated and
#           reallocated, so the heap's growth shows in it.
#   value   all programs print the answer the input has, so a reused cell holds what was
#           written to it.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${SPROUT_REUSE_BIN:-$ROOT/build/compile_driver_bin_stage1}"
STDLIB="${SPROUT_REUSE_STDLIB:-$ROOT/stdlib}"
CC_EXTRA="${SPROUT_REUSE_CC_EXTRA:-}"
SMOKE="$ROOT/tests/linear_reuse_smoke"
TMPD=$(mktemp -d /tmp/sprout_linear_reuse_XXXXXX)
trap 'rm -rf "$TMPD"' EXIT
fail=0

bad() { echo "linear-reuse-smoke: FAIL $*" >&2; fail=1; }

# The runtime is compiled once (each source -> its own .o); every build links the set.
mkdir -p "$TMPD/rtobj"
for src in "$ROOT"/runtime/*.c; do
  # shellcheck disable=SC2086
  clang -c "$src" -O2 $CC_EXTRA -o "$TMPD/rtobj/$(basename "$src" .c).o" 2> "$TMPD/rt.err" \
    || { echo "linear-reuse-smoke: runtime compile failed ($src)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
done

field() { sed -n "s/.*\[sprout alloc\].* $2=\([0-9]*\).*/\1/p" "$1"; }

# run <fixture> <placeholder> <value> <expected output>: -O2, with the allocation report in
# $TMPD/<fixture>_<value>.alloc.
run() {
  local name="$1" key="$2" val="$3" want="$4" base="$TMPD/$1_$3"
  sed "s/$key/$val/g" "$SMOKE/$name.spr" > "$base.spr"
  "$BIN" --emit-ir "$STDLIB" --package-root "$ROOT" "$base.spr" > "$base.ll" 2> "$base.err" \
    || { echo "linear-reuse-smoke: emit-IR failed for $name ($key=$val)" >&2; cat "$base.err" >&2; return 1; }
  # shellcheck disable=SC2086
  clang "$base.ll" "$TMPD"/rtobj/*.o -O2 $CC_EXTRA -o "$base" 2> "$base.err" \
    || { echo "linear-reuse-smoke: link failed for $name" >&2; cat "$base.err" >&2; return 1; }
  SPROUT_DEBUG_ALLOC=1 "$base" > "$base.out" 2> "$base.alloc" \
    || { echo "linear-reuse-smoke: $name ($key=$val) exited non-zero" >&2; return 1; }
  [ "$(cat "$base.out")" = "$want" ] || bad "$name ($key=$val) printed '$(cat "$base.out")', not '$want'"
}

# compare_delta <fixture> <placeholder> <small> <small answer> <large> <large answer> <field> <want>
# The large run's <field> minus the small run's must be <want>.
compare_delta() {
  if run "$1" "$2" "$3" "$4" && run "$1" "$2" "$5" "$6"; then
    local a b
    a=$(field "$TMPD/$1_$3.alloc" "$7"); b=$(field "$TMPD/$1_$5.alloc" "$7")
    if [ -z "$a" ] || [ -z "$b" ]; then
      bad "$1: no '$7' field in the allocation report"
    elif [ $((b - a)) -eq "$8" ]; then
      echo "linear-reuse-smoke: $1 $7 grows by $((b - a)) from $2=$3 to $2=$5"
    else
      bad "$1: $7 is $a at $2=$3 and $b at $2=$5, a difference of $((b - a)), not $8"
    fi
  else
    fail=1
  fi
}

# compare <fixture> <placeholder> <small> <small answer> <large> <large answer>: a step
# allocates nothing, so both sizes allocate the same number of objects.
compare() { compare_delta "$@" sprout_obj 0; }

# 168 primes below 1000, 17984 below 200000; 7 fields per CSV row.
compare grid NSIZE 1000 168 200000 17984
compare csv ROWS 20 140 4000 28000
# Keys 0..99 sum to 4950.
compare tree INSERTS 1000 4950 200000 4950
# Keys i*37 mod 101 for i in 1..64 sum to 3230. 64 pushes onto an empty heap take the element
# buffer to capacity 8, then 16, 32 and 64: one allocation and three reallocations. Pops never
# shrink it.
compare_delta linheap NPUSH 0 "0 ordered 0" 64 "64 ordered 3230" vector 4

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "==> linear-reuse-smoke ✓"
