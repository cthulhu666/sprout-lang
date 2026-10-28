#!/usr/bin/env bash
# docs/list-toolkit-v0.md §4.6.1: `stdlib.sort` against the list merge sorts it
# replaced. Every variant runs ROUNDS times, interleaved, at n=1k and n=1M; the
# 1M runs also record peak RSS (`/usr/bin/time -l`, macOS). Prints min, median and
# spread per row, as bench/native_runtime/bench.sh does.
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
mise exec -- just compile-native "$DIR/list_sort_bench.sprout" "$BIN/list_sort_bench" > /dev/null 2>&1

VARIANTS="sort_list usort_list sort_vec usort_vec sort_by dsort_by dsort_lean"

for r in $(seq 1 "$ROUNDS"); do
  echo "==> round $r/$ROUNDS" >&2
  for v in $VARIANTS; do
    "$BIN/list_sort_bench" "$v" 1000 500 \
      | awk '{ split($4, t, "="); printf "%s 1k\tns/elem\t%s\n", $1, t[2] }' >> "$OUT"
    /usr/bin/time -l "$BIN/list_sort_bench" "$v" 1000000 1 2> "$BIN/time.txt" \
      | awk '{ split($4, t, "="); printf "%s 1M\tns/elem\t%s\n", $1, t[2] }' >> "$OUT"
    awk -v v="$v" '/maximum resident set size/ { printf "%s 1M\tMB peak\t%.1f\n", v, $1 / 1048576 }' \
      "$BIN/time.txt" >> "$OUT"
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
