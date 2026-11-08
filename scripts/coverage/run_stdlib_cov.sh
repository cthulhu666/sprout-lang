#!/usr/bin/env bash
# Stdlib + runtime coverage over every test binary `just test` builds and runs:
# tests/stdlib/*.spr, tests/stdlib/compiler/*.spr, tests/ide/*.spr (--package-root), tests/conformance/run/*.spr,
# tests/renaming_smoke/cyclic_renaming.spr. Uses a copy of the plain stage-1 (never the swapped one).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
R="$COV_DIR/stdlib_run"
cd "$REPO"
if [[ -e "$STAGE1_BACKUP" ]]; then
  echo "ERROR: $STAGE1_BACKUP exists: stage-1 may still be the instrumented compiler." >&2; exit 1
fi
rm -rf "$R"; mkdir -p "$R/work" "$R/flags" "$R/rtprof" "$B/rt_cov"
cp "$STAGE1" "$B/stage1_plain"
clang -c -O2 "$SCRIPTS/sancov_dump.c" -o "$B/sancov_dump.o"
for f in runtime/*.c; do
  clang -c -O2 -fprofile-instr-generate -fcoverage-mapping "$f" -o "$B/rt_cov/$(basename "$f" .c).o"
done
: > "$R/inputs.tsv"
i=0
add() { printf '%s\t%s\t%s\n' "$i" "$1" "$2" >> "$R/inputs.tsv"; i=$((i+1)); }
for f in tests/stdlib/*.spr tests/stdlib/compiler/*.spr tests/ide/*.spr; do add "$f" stdlib; done
for f in tests/conformance/run/*.spr tests/renaming_smoke/cyclic_renaming.spr; do add "$f" conf; done
JOBS=${JOBS:-10}
start=$(date +%s)
awk -F'\t' '{print $1" "$2" "$3}' "$R/inputs.tsv" | xargs -P "$JOBS" -n 3 bash "$SCRIPTS/stdlib_one.sh"
echo "test binaries done ($(( $(date +%s) - start ))s)"
cat "$R"/work/*/status | awk -F'\t' '{print $2"\t"$3}' | sort | uniq -c
