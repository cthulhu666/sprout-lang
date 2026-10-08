#!/usr/bin/env bash
# GC trigger arms × workloads — docs/gc-trigger-v0.md §9 items 3 and 4.
#
# Arms, on one runtime build:
#   floor — the default trigger, footprint floor on
#   off   — SPROUT_GC_THRESHOLD=4096: today's default floor, which turns the footprint floor off
# Workloads:
#   compiler — the seed compiler emitting stdlib/compiler/ast_to_ir.sprout (wall and peak RSS too)
#   galaxy, system — uncharted-suns game/app.sprout, 1,200 frames, muted; `system` starts in-system
#                    (perf.py's `belt` flags). Needs UNSUNS_DIR and UNSUNS_CATALOG, a display and a GPU.
# Runs interleave arms (floor, off, floor, off, …) so drift lands on both. GC cost is reported per
# allocation, because the game's allocation volume varies several-fold between runs.
#
#   bench/gc_trigger/measure.sh [reps] [workload...]      # default: 3 compiler, galaxy system
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
OUT="${TMPDIR:-/tmp}/sprout_gc_trigger"
REPS="${1:-3}"; shift || true
WORKLOADS=("$@"); [ ${#WORKLOADS[@]} -gt 0 ] || WORKLOADS=(compiler galaxy system)
# Game cycles before this are the catalog load, not the steady state.
GAME_SKIP=16
mkdir -p "$OUT"

case "$(uname -s)" in
  Darwin) TIME_FLAG=-l; LINK_EXTRA=(-framework Security -framework CoreFoundation) ;;
  *)      TIME_FLAG=-v; LINK_EXTRA=(-lm -lpthread) ;;
esac

compiler_bin() {
  [ -x "$OUT/compiler" ] && return 0
  echo "==> Linking the seed compiler against $REPO/runtime ..." >&2
  clang "$REPO/bootstrap/compile_driver.ll" "$REPO"/runtime/*.c -O2 "${LINK_EXTRA[@]}" \
    -o "$OUT/compiler" 2>"$OUT/link.err" \
    || { echo "ERROR: link failed" >&2; cat "$OUT/link.err" >&2; exit 1; }
}

# <log> <skip> -> "cycles allocs gc_ms us_per_1k mean_pause_us max_regions mean_threshold"
# The game's log holds the build's compiler first; the last process (after the final cycle=1) is read.
summarise() {
  awk -v skip="$2" '
    /^\[sprout gc\] cycle=/ {
      delete v
      for (i = 1; i <= NF; i++) { split($i, kv, "="); v[kv[1]] = kv[2] }
      if (v["cycle"] == 1) { c = a = us = th = reg = 0 }
      if (v["reason"] == "atexit" || v["cycle"] < skip) next
      c++; a += v["alloc_since_gc"]; us += v["elapsed_us"]; th += v["threshold"]
      if (v["arena_regions"] + v["overflow_regions"] > reg) reg = v["arena_regions"] + v["overflow_regions"]
    }
    END {
      if (c == 0) { print "0 0 0 0 0 0 0"; exit }
      printf "%d %d %.1f %.1f %d %d %d\n", c, a, us / 1000, (a > 0 ? us * 1000 / a : 0), us / c, reg, th / c
    }' "$1"
}

# <time output> -> "wall_s rss_mb"
time_fields() {
  if [ "$TIME_FLAG" = -l ]; then
    awk '/ real / { w = $1 } /maximum resident set size/ { r = $1 / 1048576 } END { printf "%.2f %.1f\n", w, r }' "$1"
  else
    awk -F': ' '/Elapsed \(wall clock\)/ { n = split($2, p, ":"); w = p[n] + (n > 1 ? p[n-1] * 60 : 0) }
                /Maximum resident set size/ { r = $2 / 1024 } END { printf "%.2f %.1f\n", w, r }' "$1"
  fi
}

# Bash 3.2 (macOS) rejects an empty "${a[@]}" under set -u, hence the ${a[@]+…} guards below.
ARM_ENV=()
set_arm() { ARM_ENV=(); [ "$1" = off ] && ARM_ENV=(SPROUT_GC_THRESHOLD=4096); return 0; }

run_compiler() { # <arm> <rep>
  local log="$OUT/compiler.$1.$2"
  set_arm "$1"
  env -u SPROUT_GC_THRESHOLD ${ARM_ENV[@]+"${ARM_ENV[@]}"} SPROUT_DEBUG_GC=1 /usr/bin/time "$TIME_FLAG" \
    "$OUT/compiler" --emit-ir "$REPO/stdlib" "$REPO/stdlib/compiler/ast_to_ir.sprout" \
    > /dev/null 2> "$log.gc" || { echo "ERROR: compiler run failed ($1 $2)" >&2; tail -3 "$log.gc" >&2; return 1; }
  grep -v '^\[sprout gc\]' "$log.gc" > "$log.time"
  echo "compiler $1 $2 $(summarise "$log.gc" 0) $(time_fields "$log.time")"
}

run_game() { # <scene> <arm> <rep>
  : "${UNSUNS_DIR:?set UNSUNS_DIR to an uncharted-suns checkout}"
  : "${UNSUNS_CATALOG:?set UNSUNS_CATALOG to a star catalog directory}"
  local log="$OUT/$1.$2.$3.gc" flags=()
  [ "$1" = system ] && flags=(--system=00232 --ship-view)
  set_arm "$2"
  # `mise exec -- env` so SPROUT_ROOT here wins over one the game repo's mise config sets.
  (cd "$UNSUNS_DIR" && mise exec -- env -u SPROUT_GC_THRESHOLD SPROUT_ROOT="$REPO" ${ARM_ENV[@]+"${ARM_ENV[@]}"} \
    SPROUT_AUDIO_MUTE=1 SPROUT_DEBUG_GC=1 SPROUT_GFX_MAX_FRAMES=1200 \
    just run-gfx game/app.sprout "$UNSUNS_CATALOG" ${flags[@]+"${flags[@]}"}) > "$log" 2>&1 \
    || { echo "ERROR: game run failed ($1 $2 $3)" >&2; tail -3 "$log" >&2; return 1; }
  grep -q ' free=' "$log" || { echo "ERROR: $log has no free= field — the game linked another runtime" >&2; return 1; }
  echo "$1 $2 $3 $(summarise "$log" "$GAME_SKIP") - -"
}

echo "workload arm rep cycles allocs gc_ms us_per_1k_alloc mean_pause_us max_regions mean_threshold wall_s rss_mb"
for w in "${WORKLOADS[@]}"; do
  [ "$w" = compiler ] && compiler_bin
  for rep in $(seq 1 "$REPS"); do
    for arm in floor off; do
      case "$w" in
        compiler)       run_compiler "$arm" "$rep" ;;
        galaxy|system)  run_game "$w" "$arm" "$rep" ;;
        *) echo "ERROR: unknown workload $w" >&2; exit 1 ;;
      esac
    done
  done
done
