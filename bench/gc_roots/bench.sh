#!/usr/bin/env bash
# GC root-stack / allocation microbenchmark harness.
#
# Times a SINGLE runtime build. The A/B in bench/results-2026-09-20-gc-root-stack.md
# is produced by compiling the bench to IR once and linking that same IR against two
# runtime trees — interleaving the runs, because this machine is often loaded and a
# before-window/after-window comparison picks up the load instead of the change:
#
#   ./build/compile_driver_bin_stage1 --emit-ir stdlib bench/gc_roots/gc_roots_bench.sprout > b.ll
#   clang b.ll <old runtime>/*.c -O2 $EXTRA -o bench_old
#   clang b.ll runtime/*.c        -O2 $EXTRA -o bench_new
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/gc_roots_bench.sprout"
BIN="$DIR/gc_roots_bench"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: compile failed" >&2; exit 1; }

echo "==> Running (1 warm-up, discarded; then 6 timed) ..."
"$BIN" > /dev/null || { echo "ERROR: warm-up run failed" >&2; exit 1; }
for i in 1 2 3 4 5 6; do
  "$BIN" || { echo "ERROR: run $i failed" >&2; exit 1; }
done
