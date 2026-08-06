#!/usr/bin/env bash
# Transcendental-math microbenchmark harness: pure-Sprout stdlib.math vs C libm.
#
# Builds math_transcendental_bench.sprout with the current stage-1 compiler and
# libm_reference.c with clang -O2, runs both a few times warm (first discarded), and
# prints a per-call comparison with the harness baseline subtracted.
#
# The question this answers is NOT "is Sprout slower than libm" (it is — libm is
# hand-tuned assembly in places). It is "is any real workload bottlenecked by these
# functions", which is the only thing that would justify escalating away from pure
# Sprout. Per stdlib/math.sprout, that escalation would be an LLVM intrinsic
# (llvm.exp.f64), not a C runtime builtin, so a slow row here is not by itself
# an argument for touching runtime/APPROVED_BUILTINS.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
SRC="$DIR/math_transcendental_bench.sprout"
BIN="$DIR/math_transcendental_bench"
CSRC="$DIR/libm_reference.c"
CBIN="$DIR/libm_reference"
RUNS="${RUNS:-4}"

if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  echo "ERROR: build/compile_driver_bin_stage1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

echo "==> Compiling $SRC ..."
(cd "$REPO" && mise exec -- just compile-native "$SRC" "$BIN") >/dev/null 2>&1 \
  || { echo "ERROR: Sprout compile failed" >&2; exit 1; }

echo "==> Compiling $CSRC (clang -O2 -lm) ..."
clang -O2 "$CSRC" -lm -o "$CBIN" 2>/dev/null \
  || { echo "ERROR: C reference compile failed" >&2; exit 1; }

# Keep the best (lowest) time per label across runs: the minimum is the least
# noise-contaminated estimate of the true cost, where the mean just measures the
# machine's background load. The max is carried too and printed as a spread, so that
# run-to-run noise is visible instead of being silently averaged into the headline —
# an earlier 200k-rep version of this harness showed the UNCHANGED C reference swinging
# 2x between runs, which is exactly the failure a min-only report hides.
best_of() {
  local bin="$1" label_prefix="$2"
  "$bin" >/dev/null 2>&1   # discard first (warm-up)
  for _ in $(seq 1 "$RUNS"); do "$bin"; done \
    | awk -v pfx="$label_prefix" '
        /us=/ {
          split($2, a, "="); us = a[2]
          if (!($1 in best) || us < best[$1]) best[$1] = us
          if (!($1 in worst) || us > worst[$1]) worst[$1] = us
        }
        END { for (k in best) printf "%s%s %d %d\n", pfx, k, best[k], worst[k] }'
}

echo "==> Running Sprout ($RUNS warm runs after a discarded warm-up) ..."
best_of "$BIN" "sprout:" > /tmp/mt_sprout.txt
echo "==> Running C libm ($RUNS warm runs after a discarded warm-up) ..."
best_of "$CBIN" "libm:" > /tmp/mt_libm.txt

REPS=$("$CBIN" | awk -F= '/^reps=/{print $2}')

echo
printf '%-10s %14s %14s %8s %9s\n' function "sprout ns/call" "libm ns/call" ratio "noise"
printf '%-10s %14s %14s %8s %9s\n' "--------" "--------------" "------------" "-----" "-------"
awk -v reps="$REPS" '
  FNR==NR { split($1, k, ":"); s[k[2]] = $2; sw[k[2]] = $3; next }
            { split($1, k, ":"); l[k[2]] = $2; lw[k[2]] = $3 }
  END {
    # Subtract the harness baseline from every row so the figure is the call itself.
    sb = s["baseline"]; lb = l["baseline"]
    n = split("exp ln log10 cbrt pow_frac pow_int exp_wide ln_wide sqrt_wide", order, " ")
    for (i = 1; i <= n; i++) {
      f = order[i]
      sn  = (s[f] - sb) * 1000.0 / reps
      ln_ = (l[f] - lb) * 1000.0 / reps
      # Worst-case spread across runs, as a percentage of the best time. A large
      # number here means the row should not be quoted to two significant figures.
      spread = (sw[f] > 0 && s[f] > 0) ? (sw[f] - s[f]) * 100.0 / s[f] : 0
      if (sn < 0) sn = 0
      if (ln_ <= 0) printf "%-10s %14.1f %14s %8s %8.0f%%\n", f, sn, "<0.1", "n/a", spread
      else printf "%-10s %14.1f %14.1f %7.1fx %8.0f%%\n", f, sn, ln_, sn / ln_, spread
    }
    printf "\nraw baseline: sprout %d us (spread %d%%), libm %d us (spread %d%%) over %d reps\n",
           sb, (sw["baseline"]-sb)*100/sb, lb, (lw["baseline"]-lb)*100/lb, reps
  }' /tmp/mt_sprout.txt /tmp/mt_libm.txt

echo
echo "==> Checksums (Sprout vs libm — these should agree to ~1e-13 relative):"
"$BIN"  | awk '/checksum/ {printf "  sprout %-9s %s\n", $1, $3}'
"$CBIN" | awk '/checksum/ {printf "  libm   %-9s %s\n", $1, $3}'
