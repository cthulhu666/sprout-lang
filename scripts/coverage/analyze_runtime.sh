#!/usr/bin/env bash
# C runtime source-based coverage: test binaries (stdlib_run) + tests/c_runtime (crt_run).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
OUT="$COV_DIR/results"; mkdir -p "$OUT"
cd "$REPO"
llvm-profdata merge -o "$COV_DIR/rt_tests.profdata" "$COV_DIR"/stdlib_run/rtprof/*.profraw
llvm-profdata merge -o "$COV_DIR/rt_crt.profdata" "$COV_DIR"/crt_run/prof/*.profraw
llvm-profdata merge -o "$COV_DIR/rt_all.profdata" "$COV_DIR"/stdlib_run/rtprof/*.profraw "$COV_DIR"/crt_run/prof/*.profraw
# one stdlib test binary carries the runtime mapping used by every Sprout test binary
SBINS=("$COV_DIR"/stdlib_run/work/*/t.bin)   # not `ls | head -1`: SIGPIPE under pipefail
SBIN="${SBINS[0]}"
OBJS=("$SBIN")
for b in "$COV_DIR"/crt_run/bins/*; do OBJS+=(-object "$b"); done
SRCS=("$REPO"/runtime/*.c)
for p in all tests crt; do
  llvm-cov report -instr-profile="$COV_DIR/rt_$p.profdata" "${OBJS[@]}" "${SRCS[@]}" > "$OUT/runtime_report_$p.txt" 2>"$OUT/runtime_report_$p.err" || true
done
llvm-cov export -instr-profile="$COV_DIR/rt_all.profdata" "${OBJS[@]}" "${SRCS[@]}" -skip-expansions > "$OUT/runtime_export_all.json" 2>/dev/null || true
cat "$OUT/runtime_report_all.txt"
