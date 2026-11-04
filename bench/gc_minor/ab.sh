#!/usr/bin/env bash
# A/B runner for docs/gc-generational-v0.md §14.8 step 4: SPROUT_GC_MINOR=0 vs 1.
#
# Every workload binary is built ONCE up front (-O2, whole-program link via `just compile-native`),
# so no compiler run sits inside a measured run and no compiler GC log reaches a program's log
# (§13.6's first trap). Arms are interleaved in pairs and the first arm alternates (ABBA...), so
# a drifting load lands on both. One TSV row per run; `summarize.py` turns the rows into tables.
#
# usage: ab.sh [-n runs] [-s fixed|default|both|none] [-w names] [-e every,...] [-o dir] [-B]
#   -n  runs per arm (6)
#   -s  fixed:   SPROUT_GC_ADAPT_RATIO=0 + a fixed SPROUT_GC_THRESHOLD per workload. Adaptation is
#                off, but collection counts still differ after minors (the heap count then includes
#                dead old objects): compare per-collection p50/p90, not totals
#       default: no GC env besides the arm (CPU, GC time and RSS end to end)
#       both (default), none. With -e and no -s, only the -e sweep runs.
#   -w  comma-separated workload names (all)
#   -e  SPROUT_GC_MAJOR_EVERY values for MINOR=1 at default settings, each round also runs one
#       MINOR=0 baseline; the run order rotates every round
#   -o  output dir (/private/tmp/gc_minor_ab/<timestamp>)
#   -B  reuse binaries already in <dir>/bin and append to its results.tsv; without -B a
#       fresh results.tsv is started
#
# macOS only: it times with `/usr/bin/time -l`. Run from anywhere; it cds to the repo root.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
cd "$REPO"

# Not included: §5's "http server (real sockets, serve_n + wrk)" row. Its program is not in the
# repo, and wrk is time-bound (the request count, hence the allocation count, varies run to run
# and arm to arm), so cycles could not be held equal. bench/results-2026-09-30-gc-floor.md §3
# measured a wrk-driven server at +-30% rps spread, too wide to resolve a GC difference.
ALL_WORKLOADS="selfcompile compiler_test digit_recognizer gc_roots http_log_middleware nqueens astar math_transcendental mutvec_int galaxy_client"
UNSUNS="${UNSUNS:-$HOME/GameDev/uncharted-suns}"
CATALOG="${CATALOG:-$HOME/GameDev/universegen/catalog}"
COMPILER="$REPO/build/compile_driver_bin_stage1"
RUN_TIMEOUT="${RUN_TIMEOUT:-300}"   # seconds per run

RUNS=6
SETTING=""
WORKLOADS="$ALL_WORKLOADS"
WL_NAMED=0
EVERY=""
OUT="/private/tmp/gc_minor_ab/$(date +%Y%m%d-%H%M%S)"
REUSE=0

while getopts "n:s:w:e:o:B" opt; do
  case "$opt" in
    n) RUNS="$OPTARG" ;;
    s) SETTING="$OPTARG" ;;
    w) WORKLOADS="${OPTARG//,/ }"; WL_NAMED=1 ;;
    e) EVERY="${OPTARG//,/ }" ;;
    o) OUT="$OPTARG" ;;
    B) REUSE=1 ;;
    *) echo "usage: $0 [-n runs] [-s fixed|default|both|none] [-w names] [-e every,...] [-o dir] [-B]" >&2; exit 2 ;;
  esac
done
if [[ -z "$SETTING" ]]; then
  if [[ -n "$EVERY" ]]; then SETTING=none; else SETTING=both; fi
fi
case "$SETTING" in fixed|default|both|none) ;; *) echo "bad -s: $SETTING" >&2; exit 2 ;; esac
[[ "$(uname)" == "Darwin" ]] || { echo "ERROR: macOS only (/usr/bin/time -l)" >&2; exit 1; }
[[ -x "$COMPILER" ]] || { echo "ERROR: $COMPILER missing (run: mise exec -- just bootstrap-from-seed)" >&2; exit 1; }

# galaxy_client needs the game repo, raylib and a catalog: dropped with a warning when it came from
# the default set, an error when named with -w.
if [[ " $WORKLOADS " == *" galaxy_client "* ]]; then
  gc_missing=""
  for f in "$UNSUNS/game/app.sprout" "$UNSUNS/graphics/raylib-prefix.sh" "$UNSUNS/graphics/sprout_gfx.c" \
           "$UNSUNS/graphics/sprout_audio.c"; do
    [[ -f "$f" || -n "$gc_missing" ]] || gc_missing="$f"
  done
  [[ -d "$CATALOG" || -n "$gc_missing" ]] || gc_missing="$CATALOG"
  if [[ -n "$gc_missing" ]]; then
    if [[ $WL_NAMED == 1 ]]; then
      echo "ERROR: galaxy_client needs $gc_missing (set UNSUNS / CATALOG)" >&2; exit 1
    fi
    echo "WARNING: dropping galaxy_client: $gc_missing missing (set UNSUNS / CATALOG)" >&2
    WORKLOADS="${WORKLOADS/galaxy_client/}"
  fi
fi

mkdir -p "$OUT/bin" "$OUT/runs"
TSV="$OUT/results.tsv"
CLANG_EXTRA="-framework Security -framework CoreFoundation"

# ── Workloads ────────────────────────────────────────────────────────────────────────────────
# wl_def <name> sets W_SRC (source built to a binary, empty if none), W_DIR (cwd of the run),
# W_CMD (argv), W_ENV (extra env; never empty, so `set -u` is happy on bash 3.2) and W_THR
# (SPROUT_GC_THRESHOLD for the `fixed` setting).
#
# Thresholds: the collector fires when the heap OBJECT COUNT reaches SPROUT_GC_THRESHOLD, so a
# fixed value must clear the peak live set or it collects on every allocation. Each is the default
# MINOR=0 run's peak heap_after plus its mean alloc_since_gc, rounded; that gives about the default
# run's cycle count or fewer. Comments: the default run's cycles and peak live objects.
BIN="$OUT/bin"
wl_def() {
  W_SRC=""; W_DIR="$REPO"; W_ENV=("SPROUT_AB=1"); W_THR=""
  case "$1" in
    selfcompile)
      W_CMD=("$COMPILER" --emit-ir stdlib stdlib/compiler/compile_driver.sprout)
      W_THR=2200000 ;;    # 92 cycles, peak live 1.53M
    compiler_test)
      # Slowest tests/stdlib/test_*.spr importing stdlib.compiler.*, by -O2 wall time over 5 runs:
      # test_ir_codegen_match 0.54 s (19 collections), test_ir_rooting 0.45 s (50). The slower
      # test_repl_eval_link (1.2 s) spends its time in a `clang` child, so it measures no GC.
      W_SRC=tests/stdlib/test_ir_codegen_match.spr; W_CMD=("$BIN/compiler_test")
      W_THR=4400 ;;       # 19 cycles, peak live 677
    digit_recognizer)
      W_SRC=examples/digit_recognizer/recognizer.sprout; W_CMD=("$BIN/digit_recognizer")
      W_THR=48500 ;;      # 16 cycles, peak live 26.8k
    gc_roots)
      W_SRC=bench/gc_roots/gc_roots_bench.sprout; W_CMD=("$BIN/gc_roots")
      W_THR=4100 ;;       # 5,028 cycles, peak live 72
    http_log_middleware)
      W_SRC=bench/http_log_middleware/http_log_middleware_bench.sprout; W_CMD=("$BIN/http_log_middleware")
      W_THR=4100 ;;       # 50,809 cycles, peak live 45
    nqueens)
      W_SRC=examples/nqueens.sprout; W_CMD=("$BIN/nqueens")
      W_THR=4120 ;;       # 8,279 cycles, peak live 82
    astar)
      W_SRC=examples/astar.sprout; W_CMD=("$BIN/astar")
      W_THR=4200 ;;       # 140 cycles, peak live 306
    math_transcendental)
      W_SRC=bench/math_transcendental/math_transcendental_bench.sprout; W_CMD=("$BIN/math_transcendental")
      W_THR=4096 ;;       # one collection (atexit); it allocates almost nothing
    mutvec_int)
      W_SRC=bench/gc_minor/mutvec_int.sprout; W_CMD=("$BIN/mutvec_int")
      W_THR=27500 ;;      # 926 cycles, peak live 12.0k
    galaxy_client)
      # What a player runs (§13.6): game/app.sprout on the catalog, auto-closing after 1200 frames.
      # --system pins the start system: the default is a random home seeded by the clock, which
      # would give every run a different world. It runs from the game repo, which finds its
      # assets by relative path.
      W_DIR="$UNSUNS"; W_CMD=("$BIN/galaxy_client" "$CATALOG" "--system=232")
      W_ENV=("SPROUT_GFX_MAX_FRAMES=1200")
      W_THR=358000 ;;     # 72 cycles, peak live 180k
    *) echo "unknown workload: $1" >&2; exit 2 ;;
  esac
}

# ── Build, once ──────────────────────────────────────────────────────────────────────────────
# patch_galaxy <root> <grep pattern> <sed expr> [import line]: rewrite the loam/game files that match
# (none is fine); with an import line, add it before the first `import`, else after `module`.
patch_galaxy() {
  local f files
  files="$(grep -rl "$2" "$1/loam" "$1/game" || true)"
  [[ -z "$files" ]] && return 0
  while IFS= read -r f; do
    sed -i '' -E "$3" "$f" || return 1
    [[ -z "${4:-}" ]] && continue
    if grep -q '^import' "$f"; then
      awk -v l="$4" '/^import/ && !d { print l; d = 1 } { print }' "$f" > "$f.new" || return 1
    else
      awk -v l="$4" '{ print } /^module/ && !d { print l; d = 1 }' "$f" > "$f.new" || return 1
    fi
    mv "$f.new" "$f" || return 1
  done <<< "$files"
}

build_galaxy() { # mirrors uncharted-suns' `run-gfx`, minus the run; SPROUT_ROOT is this checkout
  local rl gfx_link root="$UNSUNS"
  rl="$(cd "$UNSUNS" && bash graphics/raylib-prefix.sh)"
  gfx_link="-lraylib -framework Cocoa -framework IOKit -framework CoreVideo -framework OpenGL -framework QuartzCore"
  if ! "$COMPILER" --emit-ir "$REPO/stdlib" --package-root "$root" "$root/game/app.sprout" > "$OUT/galaxy.ll" 2> "$OUT/galaxy.err"; then
    # The game repo lags this stdlib: `env.get`, `argv_get` and `args.parse_argv` now answer a
    # Result or moved out of the prelude, and `make_dirs` binds a `proc_run` Result in a Unit block.
    # Build a patched COPY of loam/ and game/ rather than touch the game repo (the patch only
    # unwraps those three calls and drops that binding), and say so.
    echo "WARNING: stock uncharted-suns does not compile against this stdlib: $(grep -m1 ERROR "$OUT/galaxy.err")" >&2
    echo "WARNING: building a patched copy in $OUT/galaxy_src (env.get, argv_get, args.parse_argv unwrapped; the proc_run Result binding dropped)" >&2
    root="$OUT/galaxy_src"
    rm -rf "$root"; mkdir -p "$root"
    cp -R "$UNSUNS/loam" "$UNSUNS/game" "$root/" || return 1
    patch_galaxy "$root" 'env\.get(' \
      's/env\.get\(([^)]*)\)/(match env.get(\1) with | Ok m -> m | Err _ -> Nothing)/g' || return 1
    patch_galaxy "$root" 'args\.parse_argv()' \
      's/args\.parse_argv\(\)/(match args.parse_argv() with | Ok a -> a | Err _ -> args.parse([]))/g' || return 1
    patch_galaxy "$root" 'argv_get(' \
      's/argv_get\(([^)]*)\)/(match argv.get(\1) with | Ok m -> m | Err _ -> Nothing)/g' \
      'import stdlib.argv as argv' || return 1
    patch_galaxy "$root" '_r <- proc_run(' \
      's/_r <- proc_run\(/proc_run(/g' || return 1
    "$COMPILER" --emit-ir "$REPO/stdlib" --package-root "$root" "$root/game/app.sprout" > "$OUT/galaxy.ll" || return 1
  fi
  # shellcheck disable=SC2086
  clang "$OUT/galaxy.ll" "$REPO"/runtime/*.c "$UNSUNS/graphics/sprout_gfx.c" "$UNSUNS/graphics/sprout_audio.c" \
    -O2 -I"$rl/include" -L"$rl/lib" $CLANG_EXTRA $gfx_link -o "$BIN/galaxy_client" || return 1
  rm -f "$OUT/galaxy.ll"
}

build_all() {
  local w
  for w in $WORKLOADS; do
    wl_def "$w"
    if [[ "$w" == galaxy_client ]]; then
      if [[ $REUSE == 1 && -x "$BIN/$w" ]]; then continue; fi
      echo "==> building $w" >&2
      build_galaxy >> "$OUT/build.log" 2>&1 || { echo "ERROR: build of $w failed, see $OUT/build.log" >&2; exit 1; }
      grep WARNING "$OUT/build.log" >&2 || true
    elif [[ -n "$W_SRC" ]]; then
      if [[ $REUSE == 1 && -x "$BIN/$w" ]]; then continue; fi
      echo "==> building $w" >&2
      mise exec -- just compile-native "$W_SRC" "$BIN/$w" >> "$OUT/build.log" 2>&1 \
        || { echo "ERROR: build of $w failed, see $OUT/build.log" >&2; exit 1; }
    fi
  done
}

# ── One run ──────────────────────────────────────────────────────────────────────────────────
# stdout is hashed after timings are blanked, so only output that should be deterministic is
# compared between arms.
norm() {
  sed -E '/\[unsuns\] ship home:/d; s/(time_us|dur_us|us)=[0-9]+/\1=T/g; s/[0-9]+(\.[0-9]+)? *(ms|us\/run|ns\/op|us)/T \2/g'
}

# Percentile of a sorted one-column file (linear interpolation, like bench/gc_pause).
pctl() {
  awk -v p="$2" '{a[NR]=$1} END{
    if (NR == 0) { print 0; exit }
    k = (NR - 1) * p / 100; lo = int(k); hi = (lo + 1 < NR) ? lo + 1 : NR - 1
    printf "%.0f\n", a[lo + 1] + (a[hi + 1] - a[lo + 1]) * (k - lo) }' "$1"
}

# Cycle lines -> "cycles minors majors gc_us marked"; steady elapsed_us (not atexit, not the first
# cycle) goes to $2. With the mode off a line has no kind= and counts as a major: it is a full one.
gc_summary() {
  grep '^\[sprout gc\] cycle=' "$1" | awk -v steady="$2" '
    function f(n,   s) {
      if (match($0, n "=-?[0-9]+")) { s = substr($0, RSTART + length(n) + 1, RLENGTH - length(n) - 1); return s + 0 }
      return 0 }
    { c++; us = f("elapsed_us"); t += us; m += f("marked")
      if ($0 ~ /kind=minor/) mi++; else ma++
      if (NR > 1 && $0 !~ /reason=atexit/) print us > steady }
    END { printf "%d %d %d %d %d\n", c, mi, ma, t, m }' || echo "0 0 0 0 0"
}

# run_one <workload> <setting> <minor 0|1> <major_every or -> <run idx>
run_one() {
  local w="$1" setting="$2" minor="$3" every="$4" idx="$5"
  wl_def "$w"
  local tag="$w.$setting.m$minor.e$every.r$idx"
  local rd="$OUT/runs/$tag"
  local envs=("${W_ENV[@]}" "SPROUT_DEBUG_GC=1" "SPROUT_GC_MINOR=$minor")
  [[ "$every" != "-" ]] && envs+=("SPROUT_GC_MAJOR_EVERY=$every")
  [[ "$setting" == fixed ]] && envs+=("SPROUT_GC_ADAPT_RATIO=0" "SPROUT_GC_THRESHOLD=$W_THR")

  local load; load="$(sysctl -n vm.loadavg | awk '{print $2}')"
  local rc=0
  # The alarm survives perl's exec, so a collection livelock ends the run (exit 142) instead of hanging.
  (cd "$W_DIR" && exec env "${envs[@]}" /usr/bin/time -l -o "$rd.time" \
    perl -e 'alarm shift; exec @ARGV' "$RUN_TIMEOUT" "${W_CMD[@]}") \
    > "$rd.out" 2> "$rd.err" || rc=$?

  local real user sys rss
  read -r real user sys < <(awk '/ real /{print $1, $3, $5}' "$rd.time")
  rss="$(awk '/maximum resident/{printf "%d", $1 / 1024}' "$rd.time")"

  local steady="$rd.steady"; : > "$steady"
  local cycles minors majors gc_us marked line
  line="$(gc_summary "$rd.err" "$steady")"
  read -r cycles minors majors gc_us marked <<< "$line"
  sort -n "$steady" -o "$steady"
  local p50 p90; p50="$(pctl "$steady" 50)"; p90="$(pctl "$steady" 90)"

  local hash; hash="$(norm < "$rd.out" | shasum | cut -c1-12)"
  # Stdout must match across arms and settings; the first TSV row of a workload is the reference.
  local ref; ref="$(awk -F'\t' -v w="$w" '$1 == w { print $18; exit }' "$TSV")"
  if [[ -n "$ref" && "$ref" != "$hash" ]]; then
    echo "!!!!!! STDOUT MISMATCH: $tag hash $hash != $ref !!!!!!" >&2
  fi
  [[ $rc != 0 ]] && echo "!!!!!! NONZERO EXIT ($rc): $tag !!!!!!" >&2

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$w" "$setting" "$minor" "$every" "$idx" "$load" "$real" "$user" "$sys" "$rss" \
    "$cycles" "$minors" "$majors" "$gc_us" "$p50" "$p90" "$marked" "$hash" "$rc" >> "$TSV"
  printf '  %-20s %-8s minor=%s every=%-5s run=%s real=%ss load=%s cycles=%s\n' \
    "$w" "$setting" "$minor" "$every" "$idx" "$real" "$load" "$cycles" >&2

  # Large outputs (the IR of a self-compile) are kept only as a hash.
  [[ "$(stat -f %z "$rd.out")" -gt 1048576 ]] && : > "$rd.out"
  rm -f "$steady"
}

# ── Schedules ────────────────────────────────────────────────────────────────────────────────
run_pairs() { # <workload> <setting>
  local w="$1" setting="$2" i first second
  for ((i = 1; i <= RUNS; i++)); do
    if ((i % 2 == 1)); then first=0; second=1; else first=1; second=0; fi
    run_one "$w" "$setting" "$first" - "$i"
    run_one "$w" "$setting" "$second" - "$i"
  done
}

run_every() { # <workload>: per round one MINOR=0 baseline and one MINOR=1 run per value
  local w="$1" i k n arms=("0:-") e
  for e in $EVERY; do arms+=("1:$e"); done
  n=${#arms[@]}
  for ((i = 1; i <= RUNS; i++)); do
    for ((k = 0; k < n; k++)); do
      local a="${arms[$(((k + i - 1) % n))]}"
      run_one "$w" every "${a%%:*}" "${a#*:}" "$i"
    done
  done
}

# ── Main ─────────────────────────────────────────────────────────────────────────────────────
START=$SECONDS
if [[ $REUSE == 0 || ! -s "$TSV" ]]; then
  printf 'workload\tsetting\tminor\tmajor_every\trun\tload\treal\tuser\tsys\tmax_rss_kb\tcycles\tminors\tmajors\tgc_us_total\tgc_us_steady_p50\tgc_us_steady_p90\tmarked_total\tstdout_hash\texit\n' > "$TSV"
fi
echo "==> output: $OUT" >&2
build_all
for w in $WORKLOADS; do
  echo "==> $w" >&2
  case "$SETTING" in fixed|both) run_pairs "$w" fixed ;; esac
  case "$SETTING" in default|both) run_pairs "$w" default ;; esac
  [[ -n "$EVERY" ]] && run_every "$w"
done
echo "==> done in $((SECONDS - START)) s (builds included); rows in $TSV" >&2
python3 -I "$DIR/summarize.py" "$TSV"
