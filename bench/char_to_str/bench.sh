#!/usr/bin/env bash
# `char_to_str` as the C builtin (baseline) against `char_to_string(char_from_codepoint(cp))` in
# Sprout (candidate). Same bench source, two binaries, run interleaved so machine load lands on
# both alike. Prints min, median and spread per row, as bench/native_runtime/bench.sh does.
#
# usage: bench.sh build <name>      build bin/<name> from the checked-out tree
#        bench.sh run [rounds]      compare bin/baseline with bin/candidate (default 15)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
OUT="$BIN/samples.tsv"
mkdir -p "$BIN"

# UTF-8 length in bytes, iterations
ROWS=(
  "1 10000000"
  "2 10000000"
  "3 10000000"
  "4 10000000"
)

case "${1:-}" in
  build)
    cd "$REPO"
    mise exec -- just compile-native "$DIR/char_to_str_bench.sprout" "$BIN/${2:?name}" > /dev/null 2>&1
    ;;
  run)
    ROUNDS="${2:-15}"
    : > "$OUT"
    echo "load: $(uptime | sed 's/.*load averages*: //')" >&2
    for r in $(seq 1 "$ROUNDS"); do
      echo "==> round $r/$ROUNDS" >&2
      for row in "${ROWS[@]}"; do
        for v in baseline candidate; do
          # shellcheck disable=SC2086
          "$BIN/$v" $row | awk -v v="$v" '{ split($4, t, "="); printf "%s %s\t%s\t%s\n", $1, v, "ns/call", t[2] }' >> "$OUT"
        done
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
