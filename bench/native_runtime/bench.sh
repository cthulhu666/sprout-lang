#!/usr/bin/env bash
# Phase 0 baseline for docs/native-sprout-runtime-v0.md: the merge-sort pair and the
# workload set, each run ROUNDS times, interleaved, so machine load lands on every
# row alike. Prints min, median and spread per row; the spread is that row's noise
# floor, and later gates are stated against it.
#
# usage: bench.sh [rounds]   (default 7)
#
# Sprout binaries are built with `just compile-native` (whole-program link), the
# C half with `clang -O2`. The self-compile row times build/compile_driver_bin_stage1,
# as `just bootstrap-from-seed` builds it.
set -euo pipefail

ROUNDS="${1:-7}"
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
OUT="$BIN/samples.tsv"
mkdir -p "$BIN"
: > "$OUT"

cd "$REPO"
mise exec -- just bootstrap-from-seed > /dev/null
echo "==> building" >&2
clang -O2 "$DIR/sort_c.c" -o "$BIN/sort_c"
build() { mise exec -- just compile-native "$1" "$BIN/$2" > /dev/null 2>&1 || { echo "build failed: $1" >&2; exit 1; }; }
build "$DIR/sort_bench.sprout" sort_sprout
build examples/nqueens.sprout nqueens
build bench/gc_roots/gc_roots_bench.sprout gc_roots
build bench/http_log_middleware/http_log_middleware_bench.sprout http_log_middleware

now_ms() { perl -MTime::HiRes=time -e 'printf "%.1f\n", time() * 1000'; }

# Wall time of one process, in ms.
wall() {
  local name="$1"; shift
  local t0 t1
  t0=$(now_ms)
  "$@" > /dev/null 2>&1 || { echo "run failed: $name" >&2; exit 1; }
  t1=$(now_ms)
  printf '%s\tms\t%s\n' "$name" "$(echo "$t1 - $t0" | bc)" >> "$OUT"
}

for r in $(seq 1 "$ROUNDS"); do
  echo "==> round $r/$ROUNDS" >&2
  "$BIN/sort_c" | awk '{ split($3, n, "="); split($4, v, "="); printf "sort_c %s\tns/elem\t%s\n", n[2], v[2] }' >> "$OUT"
  "$BIN/sort_sprout" | awk '{ split($3, n, "="); split($4, v, "="); printf "%s %s\tns/elem\t%s\n", $2, n[2], v[2] }' >> "$OUT"
  wall self_compile build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout
  wall nqueens "$BIN/nqueens"
  wall gc_roots "$BIN/gc_roots"
  wall http_log_middleware "$BIN/http_log_middleware"
done

# One row per name: min, median, and spread = (median - min) / min.
sort -t$'\t' -k1,1 -k3,3g "$OUT" | awk -F'\t' '
  { k = $1; unit[k] = $2; v[k, ++c[k]] = $3; if (!(k in seen)) { seen[k] = 1; order[++m] = k } }
  END {
    printf "| row | unit | min | median | spread |\n|---|---|---:|---:|---:|\n"
    for (i = 1; i <= m; i++) {
      k = order[i]; n = c[k]
      med = (n % 2) ? v[k, (n + 1) / 2] : (v[k, n / 2] + v[k, n / 2 + 1]) / 2
      printf "| %s | %s | %.1f | %.1f | %.1f%% |\n", k, unit[k], v[k, 1], med, (med - v[k, 1]) * 100 / v[k, 1]
    }
  }'
