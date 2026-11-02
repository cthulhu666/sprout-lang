#!/usr/bin/env bash
# `str_byte_at` against the ways to read a String's bytes without it (docs/string-byte-read-v0.md
# §6). Baseline and candidate are separate rows of ONE source, built into two binaries from the
# checked-out tree: bin/whole (whole-program link, `just compile-native`) and bin/split (separate
# units, linked as `just test` links). Each round runs every row on both binaries, interleaved so
# machine load lands on both alike. Prints min, median and spread per (binary, row), then the
# verdict per pair: the candidate wins when its median <= the baseline's median x (1 + the
# baseline's spread). Then the checksums, which must agree within each pair.
#
# usage: bench.sh build             build bin/whole and bin/split from the checked-out tree
#        bench.sh run [rounds]      run every row on both binaries (default 15)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
OUT="$BIN/samples.tsv"
SRC="$DIR/str_byte_at_bench.sprout"
mkdir -p "$BIN"

# row, iterations (a pair shares its count, so the checksums compare)
ROWS=(
  "probe_num 400"
  "byte_num 400"
  "copy_sum 3000"
  "byte_sum 3000"
  "c_strlen 5000"
  "byte_strlen 5000"
  "probe_parse_int 3000000"
  "byte_parse_int 3000000"
)

case "${1:-}" in
  build)
    cd "$REPO"
    mise exec -- just compile-native "$SRC" "$BIN/whole" > /dev/null 2>&1
    EXTRA=""
    if [ "$(uname)" = "Darwin" ]; then EXTRA="-framework Security -framework CoreFoundation"; fi
    build/compile_driver_bin_stage1 --emit-ir stdlib --package-root "$REPO" "$SRC" > "$BIN/split.ll"
    # shellcheck disable=SC2086
    clang "$BIN/split.ll" runtime/*.c -O2 $EXTRA -o "$BIN/split" 2> /dev/null
    rm -f "$BIN/split.ll"
    ;;
  run)
    ROUNDS="${2:-15}"
    : > "$OUT"
    cd "$REPO"
    echo "load: $(uptime | sed 's/.*load averages*: //')" >&2
    for r in $(seq 1 "$ROUNDS"); do
      echo "==> round $r/$ROUNDS" >&2
      for row in "${ROWS[@]}"; do
        for v in whole split; do
          # shellcheck disable=SC2086
          "$BIN/$v" $row | awk -v v="$v" '{ split($3, o, "="); split($4, t, "="); printf "%s\t%s\t%s\t%s\n", v, $1, t[2], o[2] }' >> "$OUT"
        done
      done
    done
    echo "load: $(uptime | sed 's/.*load averages*: //')" >&2
    sort -t$'\t' -k1,1 -k2,2 -k3,3g "$OUT" | awk -F'\t' '
      function med_of(k,   n) { n = c[k]; return (n % 2) ? v[k, (n + 1) / 2] : (v[k, n / 2] + v[k, n / 2 + 1]) / 2 }
      { k = $1 SUBSEP $2; v[k, ++c[k]] = $3; if (!(k in seen)) { seen[k] = 1; order[++m] = k }
        o[k, $4] = 1; if (!((k, $4) in os)) { os[k, $4] = 1; outs[k] = outs[k] " " $4; nout[k]++ } }
      END {
        printf "| binary | row | unit | min | median | spread |\n|---|---|---|---:|---:|---:|\n"
        for (i = 1; i <= m; i++) {
          k = order[i]; split(k, kk, SUBSEP)
          unit = (kk[2] ~ /parse_int/) ? "ns/call" : "ns/byte"
          med = med_of(k); mn = v[k, 1]
          M[k] = med; S[k] = (med - mn) / mn
          printf "| %s | %s | %s | %.3f | %.3f | %.1f%% |\n", kk[1], kk[2], unit, mn, med, S[k] * 100
        }
        print ""
        printf "| binary | pair | baseline median | x(1+spread) bound | candidate median | verdict |\n|---|---|---:|---:|---:|---|\n"
        np = split("probe_num:byte_num copy_sum:byte_sum c_strlen:byte_strlen probe_parse_int:byte_parse_int", pairs, " ")
        nb = split("whole split", bins, " ")
        for (b = 1; b <= nb; b++) for (p = 1; p <= np; p++) {
          split(pairs[p], pp, ":")
          bk = bins[b] SUBSEP pp[1]; ck = bins[b] SUBSEP pp[2]
          bound = M[bk] * (1 + S[bk])
          printf "| %s | %s vs %s | %.3f | %.3f | %.3f | %s |\n", bins[b], pp[1], pp[2], M[bk], bound, M[ck], (M[ck] <= bound) ? "WIN" : "LOSE"
        }
        print ""
        print "checksums (distinct out= values per binary/row; one value each, equal within a pair):"
        for (i = 1; i <= m; i++) { k = order[i]; split(k, kk, SUBSEP); printf "%s %s:%s%s\n", kk[1], kk[2], outs[k], (nout[k] > 1) ? "  <-- UNSTABLE" : "" }
      }'
    ;;
  *)
    echo "usage: bench.sh build | run [rounds]" >&2
    exit 2
    ;;
esac
