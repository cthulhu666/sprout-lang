#!/usr/bin/env bash
# docs/list-toolkit-v0.md §4.7: the generic `max` against `imath.max` and a written-out
# `>=` in Kadane's loop. Every variant runs ROUNDS times, interleaved. Prints min,
# median and spread per row, as bench/list_sort/bench.sh does.
#
# usage: bench.sh [rounds]   (default 7)
set -euo pipefail

ROUNDS="${1:-7}"
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
OUT="$BIN/samples.tsv"
mkdir -p "$BIN"
: > "$OUT"

cd "$REPO"
mise exec -- just compile-native "$DIR/min_max_bench.sprout" "$BIN/min_max_bench" > /dev/null 2>&1

VARIANTS="generic int inline"

for r in $(seq 1 "$ROUNDS"); do
  echo "==> round $r/$ROUNDS" >&2
  for v in $VARIANTS; do
    "$BIN/min_max_bench" "$v" 100000 200 \
      | awk '{ split($4, t, "="); printf "%s 100k\tns/elem\t%s\n", $1, t[2] }' >> "$OUT"
  done
done

sort -t$'\t' -k1,1 -k2,2 -k3,3g "$OUT" | awk -F'\t' '
  { k = $1 "\t" $2; v[k, ++c[k]] = $3; if (!(k in seen)) { seen[k] = 1; order[++m] = k } }
  END {
    printf "| row | unit | min | median | spread |\n|---|---|---:|---:|---:|\n"
    for (i = 1; i <= m; i++) {
      k = order[i]; n = c[k]; split(k, p, "\t")
      med = (n % 2) ? v[k, (n + 1) / 2] : (v[k, n / 2] + v[k, n / 2 + 1]) / 2
      printf "| %s | %s | %.1f | %.1f | %.1f%% |\n", p[1], p[2], v[k, 1], med, (med - v[k, 1]) * 100 / v[k, 1]
    }
  }'
