#!/usr/bin/env bash
# Build the instrumented compiler: current compiler source -> IR (stage-1) -> sancov+PGO -> link with plain -O2 runtime.
# Always rebuilt: a cached cd.ll would describe an older compiler than the one measured.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
mkdir -p "$B/rt_plain"
cd "$REPO"
clang -c -O2 "$SCRIPTS/sancov_dump.c" -o "$B/sancov_dump.o"
rm -f "$B/compiler.pcs" "$B/compile_driver_inst.nm"
"$STAGE1" --emit-ir stdlib stdlib/compiler/compile_driver.sprout > "$B/cd.ll"
bash "$SCRIPTS/instrument_ll.sh" "$B/cd.ll" "$B/cd_inst.o" 1
for f in runtime/*.c; do clang -c -O2 "$f" -o "$B/rt_plain/$(basename "$f" .c).o"; done
clang -fprofile-instr-generate "$B/cd_inst.o" "$B/sancov_dump.o" "$B/rt_plain"/*.o \
  -framework Security -framework CoreFoundation -o "$B/compile_driver_inst"
echo "built $B/compile_driver_inst"
