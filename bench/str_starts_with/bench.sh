#!/usr/bin/env bash
# `str_starts_with` as the C builtin (baseline) against `str_starts_with_at_byte(s, 0, prefix)`
# in Sprout (candidate), plus one self-compile row: the stage-1 compiler of each tree compiling
# stdlib/compiler/compile_driver.sprout. Same bench source, two binaries, run interleaved so
# machine load lands on both alike. Prints min, median and spread per row, as
# bench/native_runtime/bench.sh does.
#
# The builtin is gone after the commit that moved it, so build the baseline from that commit's
# parent.
#
# usage: bench.sh build <name>      build bin/<name> and bin/<name>_compiler from the checked-out tree
#        bench.sh run [rounds]      compare bin/baseline with bin/candidate (default 15)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
OUT="$BIN/samples.tsv"
mkdir -p "$BIN"

# row, iterations
ROWS=(
  "hit 100000000"
  "miss 300000000"
  "long 5000000"
  "empty 150000000"
)

case "${1:-}" in
  build)
    cd "$REPO"
    NAME="${2:?name}"
    mise exec -- just compile-native "$DIR/str_starts_with_bench.sprout" "$BIN/$NAME" > /dev/null 2>&1
    rm -f build/compile_driver_bin_stage1
    mise exec -- just bootstrap-from-seed > /dev/null 2>&1
    cp build/compile_driver_bin_stage1 "$BIN/${NAME}_compiler"
    ;;
  run)
    ROUNDS="${2:-15}"
    : > "$OUT"
    cd "$REPO"
    echo "load: $(uptime | sed 's/.*load averages*: //')" >&2
    for r in $(seq 1 "$ROUNDS"); do
      echo "==> round $r/$ROUNDS" >&2
      for row in "${ROWS[@]}"; do
        for v in baseline candidate; do
          # shellcheck disable=SC2086
          "$BIN/$v" $row | awk -v v="$v" '{ split($4, t, "="); printf "%s %s\t%s\t%s\n", $1, v, "ns/call", t[2] }' >> "$OUT"
        done
      done
      for v in baseline candidate; do
        t0=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
        "$BIN/${v}_compiler" --emit-ir stdlib stdlib/compiler/compile_driver.sprout > /dev/null
        t1=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
        awk -v v="$v" -v a="$t0" -v b="$t1" 'BEGIN { printf "selfcompile %s\tms/run\t%.1f\n", v, (b - a) * 1000 }' >> "$OUT"
      done
    done
    echo "load: $(uptime | sed 's/.*load averages*: //')" >&2
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
    ;;
  *)
    echo "usage: bench.sh build <name> | run [rounds]" >&2
    exit 2
    ;;
esac
