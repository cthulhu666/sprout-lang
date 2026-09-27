#!/usr/bin/env bash
# GC pause harness — reproduces docs/gc-generational-v0.md §13.
#
# Every other GC measurement in this repo counts work (marks, slots, cycles).
# This one measures the wall time of a single collection, which is what a frame
# budget cares about. No new instrument: SPROUT_DEBUG_GC=1 already logs
# elapsed_us per cycle and pause_stats.py summarises it.
#
# Three questions, in order:
#   1. where do the workloads sit today
#   2. does pause track heap size or the live set   (grow the heap, hold live flat)
#   3. can the knobs lower it once the live set is big        (vary adapt factor)
#
# Each cell is the MINIMUM of each statistic over 5 runs: load can only add time,
# so the minimum is the summary that converges. The tail past p99 is machine noise
# on a laptop and does not reproduce — see §13.4 before quoting a worst case.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="${TMPDIR:-/tmp}/sprout_gc_pause"
STATS="$DIR/pause_stats.py"

mkdir -p "$BIN"
cd "$REPO"

build() { # <source> <name>
  if mise exec -- just compile-native "$1" "$BIN/$2" >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: could not compile $1" >&2
  exit 1
}

echo "==> Compiling workloads ..."
build bench/gc_roots/gc_roots_bench.sprout                       gc_roots
build bench/http_log_middleware/http_log_middleware_bench.sprout http
build examples/nqueens.sprout                                    nqueens
build examples/digit_recognizer/recognizer.sprout                recognizer
build tests/stdlib/test_gc_age_retain_all.spr                    retain_all

echo
echo "=== 1. default settings (microseconds per collection) ==="
python3 "$STATS" --reps 5 "gc_roots (game tick)"  "$BIN/gc_roots"
python3 "$STATS" --reps 5 "http_log_middleware"   "$BIN/http"
python3 "$STATS" --reps 5 "nqueens (search)"      "$BIN/nqueens"
python3 "$STATS" --reps 5 "digit_recognizer (ML)" "$BIN/recognizer"
python3 "$STATS" --reps 5 "age_retain_all (150k)" "$BIN/retain_all"

echo
echo "=== 2. constant live set, growing heap (gc_roots holds ~70 live either way) ==="
for t in 4096 40960 409600 4096000; do
  python3 "$STATS" --reps 5 "threshold=$t" "$BIN/gc_roots" "SPROUT_GC_THRESHOLD=$t"
done

echo
echo "=== 3. large live set vs the adapt factor (the floor no knob lowers) ==="
for f in 1.5 2 3 6; do
  python3 "$STATS" --reps 5 "factor=$f" "$BIN/retain_all" "SPROUT_GC_ADAPT_FACTOR=$f"
done
