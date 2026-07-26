#!/usr/bin/env bash
# Concrete-instance devirtualization microbenchmark harness.
#
# Compiles bench/dispatch/dispatch_bench.sprout with the current stage-1 compiler
# and runs it a few times warm, printing the self-reported wall time. This times a
# SINGLE compiler; the pre-vs-post-devirt A/B in bench/results-2026-07-26.md is
# produced by building the compiler once from the current seed and once from a
# pre-devirt seed (git show <baseline>:bootstrap/compile_driver.ll > the seed,
# delete build/compile_driver_bin_stage1, rebuild from seed) and running this
# harness against each.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/dispatch_bench.sprout"
BIN="$DIR/dispatch_bench"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: compile failed" >&2; exit 1; }

echo "==> Running (6 warm; first discarded) ..."
for i in 1 2 3 4 5 6; do
  "$BIN"
done
