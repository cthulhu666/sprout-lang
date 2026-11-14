#!/usr/bin/env bash
# Record an allocation trace per GC workload and replay it under every placement policy
# (docs/gc-stage2-v0.md §5.7; the policies and the trace format are in replay.c).
#
# usage: run.sh <output dir>
#   WORKLOADS="a b"  workloads to run (default: ALL_WORKLOADS below)
#   KEEP=1           keep the traces (they can be GBs); default deletes each after its replay
#   REUSE=1          reuse binaries already in <dir>/bin
#   RUN_TIMEOUT=s    seconds per recorded run (1800)
#
# Each workload runs at the default GC settings with SPROUT_GC_REGION_LISTS=1, so the trace's
# region counts are comparable with the `base` policy. The runtime is built with
# -DSPROUT_GC_ALLOCTRACE and linked per workload with a plain clang -O2 link (not the
# whole-program link of `just compile-native`: placement does not depend on inlining).
# Each run also sets SPROUT_DEBUG_GC=1: the same run's cycle lines give today's walked= slots per
# collection kind, saved as <dir>/<workload>.walked.txt and handed to the replay.
# <dir>/<workload>.txt holds the check line, the program's stdout, the tables (calibration line
# first) and the replay's time and peak RSS.
# Exit status 1 when a replay fails or `base` does not replay the trace exactly (regions, large
# regions and free slots at every collection).
#
# Workloads mirror bench/gc_minor/ab.sh's wl_def.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
cd "$REPO"

OUT="${1:-}"
[[ -n "$OUT" ]] || { echo "usage: $0 <output dir>" >&2; exit 2; }
mkdir -p "$OUT/bin" "$OUT/rt"
OUT="$(cd "$OUT" && pwd)"
BIN="$OUT/bin"

ALL_WORKLOADS="selfcompile compiler_test digit_recognizer gc_roots http_log_middleware nqueens astar mutvec_int load_spike
  rotate_classes rotate_classes_mixed rotate_classes_cap mutvec_push inline_tail_reuse"
WORKLOADS="${WORKLOADS:-$ALL_WORKLOADS}"
KEEP="${KEEP:-0}"
REUSE="${REUSE:-0}"
RUN_TIMEOUT="${RUN_TIMEOUT:-1800}"
COMPILER="$REPO/build/compile_driver_bin_stage1"
REPLAY="$OUT/replay"

case "$(uname)" in
  Darwin) TIME_FLAG=-l; CLANG_EXTRA="-framework Security -framework CoreFoundation" ;;
  Linux) TIME_FLAG=-v; CLANG_EXTRA="" ;;
  *) echo "ERROR: unsupported OS $(uname) (macOS and Linux only)" >&2; exit 1 ;;
esac

# wl_def <name> sets W_SRC (source built to a binary; empty for selfcompile, which is the seed
# linked with the tracing runtime), W_DIR (cwd of the run), W_CMD (argv) and W_ENV (extra env).
wl_def() {
  W_SRC=""; W_DIR="$REPO"; W_ENV=("SPROUT_AB=1")
  case "$1" in
    selfcompile)
      W_CMD=("$BIN/selfcompile" --emit-ir stdlib stdlib/compiler/compile_driver.sprout) ;;
    compiler_test)
      W_SRC=tests/stdlib/test_ir_codegen_match.spr; W_CMD=("$BIN/compiler_test") ;;
    digit_recognizer)
      W_SRC=examples/digit_recognizer/recognizer.sprout; W_CMD=("$BIN/digit_recognizer") ;;
    gc_roots)
      W_SRC=bench/gc_roots/gc_roots_bench.sprout; W_CMD=("$BIN/gc_roots") ;;
    http_log_middleware)
      W_SRC=bench/http_log_middleware/http_log_middleware_bench.sprout; W_CMD=("$BIN/http_log_middleware") ;;
    nqueens)
      W_SRC=examples/nqueens.sprout; W_CMD=("$BIN/nqueens") ;;
    astar)
      W_SRC=examples/astar.sprout; W_CMD=("$BIN/astar") ;;
    mutvec_int)
      W_SRC=bench/gc_minor/mutvec_int.sprout; W_CMD=("$BIN/mutvec_int") ;;
    load_spike)
      W_SRC=bench/gc_minor/load_spike.sprout; W_CMD=("$BIN/load_spike") ;;
    rotate_classes)   # bench/gc_trigger/rotate_classes.sprout as it stands: 4 classes in turn, live data grows
      W_SRC=bench/gc_trigger/rotate_classes.sprout; W_CMD=("$BIN/rotate_classes") ;;
    rotate_classes_mixed)   # main: mixed = true
      W_SRC=bench/gc_place/rotate_classes_mixed.sprout; W_CMD=("$BIN/rotate_classes_mixed") ;;
    rotate_classes_cap)   # main: cap = 100, live data flat
      W_SRC=bench/gc_place/rotate_classes_cap.sprout; W_CMD=("$BIN/rotate_classes_cap") ;;
    mutvec_push)
      W_SRC=bench/gc_place/mutvec_push.sprout; W_CMD=("$BIN/mutvec_push") ;;
    inline_tail_reuse)
      W_SRC=tests/gc_gen/inline_tail_reuse.spr; W_CMD=("$BIN/inline_tail_reuse") ;;
    *) echo "unknown workload: $1" >&2; exit 2 ;;
  esac
}

# walked_by_kind <stderr of a SPROUT_DEBUG_GC=1 run>: "<k> <name> n=<cycles> mean=<walked> max=<walked>" per
# collection kind (0 minors off, 1 major, 2 young-only minor, 3 full-sweep minor), from the cycle lines.
walked_by_kind() {
  awk '
    /^\[sprout gc\] cycle=/ {
      k = 0
      if ($0 ~ / kind=major/) k = 1
      else if ($0 ~ / kind=minor sweep=young/) k = 2
      else if ($0 ~ / kind=minor sweep=full/) k = 3
      if (match($0, /walked=[0-9]+/)) {
        w = substr($0, RSTART + 7, RLENGTH - 7) + 0
        n[k]++; sum[k] += w
        if (w > mx[k]) mx[k] = w
      }
    }
    END {
      split("off major minor_young minor_full", nm, " ")
      for (k = 0; k < 4; k++) if (n[k] > 0) printf "%d %s n=%d mean=%.2f max=%.0f\n", k, nm[k + 1], n[k], sum[k] / n[k], mx[k]
    }' "$1"
}

# "real max_rss_mib" from a `/usr/bin/time` report, BSD `-l` (bytes) or GNU `-v` (KiB), told by format.
time_fields() {
  awk '
    / real .* user .* sys/ { real = $1 }
    /maximum resident set size/ { rss = $1 / 1048576 }
    /Elapsed \(wall clock\)/ {
      t = $0; sub(/.*\): /, "", t)
      n = split(t, p, ":")
      real = (n == 3) ? p[1] * 3600 + p[2] * 60 + p[3] : p[1] * 60 + p[2]
    }
    /Maximum resident set size \(kbytes\)/ { rss = $NF / 1024 }
    END { printf "%s %d\n", real, rss }' "$1"
}

if command -v mise > /dev/null && [[ -f "$REPO/justfile" ]]; then
  mise exec -- just bootstrap-from-seed > /dev/null \
    || { echo "ERROR: just bootstrap-from-seed failed" >&2; exit 1; }
fi
[[ -x "$COMPILER" ]] || { echo "ERROR: $COMPILER missing (run: mise exec -- just bootstrap-from-seed)" >&2; exit 1; }

echo "==> building replay and the tracing runtime" >&2
clang "$DIR/replay.c" -O2 -Wall -Wextra -o "$REPLAY"
"$REPLAY" --selftest > /dev/null || { echo "ERROR: replay --selftest failed" >&2; "$REPLAY" --selftest >&2; exit 1; }
for src in "$REPO"/runtime/*.c; do
  clang -c "$src" -O2 -DSPROUT_GC_ALLOCTRACE -o "$OUT/rt/$(basename "$src" .c).o"
done

build_all() {
  local w
  for w in $WORKLOADS; do
    wl_def "$w"
    [[ "$REUSE" == 1 && -x "$BIN/$w" ]] && continue
    echo "==> building $w" >&2
    if [[ -z "$W_SRC" ]]; then
      # shellcheck disable=SC2086
      clang "$REPO/bootstrap/compile_driver.ll" "$OUT"/rt/*.o -O2 $CLANG_EXTRA -o "$BIN/$w"
    else
      "$COMPILER" --emit-ir "$REPO/stdlib" --package-root "$REPO" "$W_SRC" > "$OUT/$w.ll"
      # shellcheck disable=SC2086
      clang "$OUT/$w.ll" "$OUT"/rt/*.o -O2 $CLANG_EXTRA -o "$BIN/$w"
      rm -f "$OUT/$w.ll"
    fi
  done
}

build_all

FAILED=0
for w in $WORKLOADS; do
  wl_def "$w"
  trace="$OUT/$w.spat"
  txt="$OUT/$w.txt"
  echo "==> recording $w" >&2
  rc=0
  # The alarm survives perl's exec, so a livelock ends the run instead of hanging.
  (cd "$W_DIR" && exec env "${W_ENV[@]}" SPROUT_GC_ALLOCTRACE="$trace" SPROUT_GC_REGION_LISTS=1 SPROUT_DEBUG_GC=1 \
    perl -e 'alarm shift; exec @ARGV' "$RUN_TIMEOUT" "${W_CMD[@]}") > "$OUT/$w.out" 2> "$OUT/$w.err" || rc=$?
  if [[ $rc != 0 ]]; then
    echo "ERROR: $w exited $rc (see $OUT/$w.err)" >&2
    FAILED=1
    rm -f "$trace"
    continue
  fi
  walked_by_kind "$OUT/$w.err" > "$OUT/$w.walked.txt"
  {
    echo "# $w: trace $(wc -c < "$trace" | tr -d ' ') bytes; stdout: $(head -c 200 "$OUT/$w.out" | head -1)"
    if ! "$REPLAY" --check "$trace"; then echo "# --check FAILED"; fi
  } > "$txt" 2>&1
  if grep -q 'FAILED' "$txt"; then
    cat "$txt" >&2
    FAILED=1
    [[ "$KEEP" == 1 ]] || rm -f "$trace"
    continue
  fi
  echo "==> replaying $w" >&2
  rc=0
  /usr/bin/time "$TIME_FLAG" -o "$OUT/$w.time" "$REPLAY" --policies all --walked "$OUT/$w.walked.txt" "$trace" >> "$txt" 2>> "$txt" || rc=$?
  read -r real rss < <(time_fields "$OUT/$w.time")
  echo "# replay: ${real}s wall, peak RSS ${rss} MiB" >> "$txt"
  [[ $rc == 0 ]] || { echo "ERROR: replay of $w failed" >&2; FAILED=1; }
  grep -q 'CALIBRATION MISS' "$txt" && { echo "ERROR: $w: base does not replay the trace exactly" >&2; FAILED=1; }
  rm -f "$OUT/$w.err"
  [[ "$KEEP" == 1 ]] || rm -f "$trace"
  cat "$txt"
done

exit "$FAILED"
