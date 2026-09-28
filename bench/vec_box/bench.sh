#!/usr/bin/env bash
# Maybe-box microbenchmark harness.
#
# Compiles bench/vec_box/vec_box_bench.sprout with the current stage-1 compiler
# and runs it a few times warm. Unlike the unboxed_read harness this needs no
# ON/OFF compiler A/B: all four read shapes are in the one binary, so a single
# run is the whole comparison.
#
# Findings and how to read the table: bench/results-2026-09-28-vec-box-tax.md.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/vec_box_bench.sprout"
BIN="$DIR/vec_box_bench"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: compile failed" >&2; exit 1; }

echo "==> Running (3 warm; first discarded) ..."
for _ in 1 2 3 4; do
  "$BIN"
done
