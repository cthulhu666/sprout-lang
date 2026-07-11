#!/usr/bin/env bash
# Unboxed-read microbenchmark harness (Phase D B2 reach extension witness).
#
# Compiles bench/unboxed_read/unboxed_read_bench.sprout with the current stage-1
# compiler and runs it a few times warm, printing the self-reported wall time.
# This times a SINGLE compiler; the ON-vs-OFF A/B in
# bench/results-2026-07-11-unboxed-reach.md is produced by building the compiler
# once with the extension and once without (stash the ir_rooting change, rebuild
# from seed) and running this harness against each.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/unboxed_read_bench.sprout"
BIN="$DIR/unboxed_read_bench"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: compile failed" >&2; exit 1; }

echo "==> Running (5 warm; first discarded) ..."
for i in 1 2 3 4 5 6; do
  "$BIN"
done
