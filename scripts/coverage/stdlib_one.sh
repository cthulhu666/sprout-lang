#!/usr/bin/env bash
# stdlib_one.sh <idx> <file> <kind: stdlib|conf>
# Emit IR (plain stage-1), sancov-instrument it, link with the coverage-instrumented runtime, run.
# Keeps: <idx>.nm (function symbols), <idx>.pcs (PC table), flags/<idx>.<pid>.flags, <idx>.ll, <idx>.log.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
R="$COV_DIR/stdlib_run"
CC="$B/stage1_plain"
idx="$1"; f="$2"; kind="$3"
W="$R/work/$idx"; mkdir -p "$W"
cd "$REPO"
st=ok
if [[ "$kind" == stdlib ]]; then
  "$CC" --emit-ir stdlib --package-root "$REPO" "$f" > "$W/t.ll" 2>"$W/emit.err" || st=emit_fail
else
  "$CC" --emit-ir stdlib "$f" > "$W/t.ll" 2>"$W/emit.err" || st=emit_fail
  grep -qE "^([0-9]+:[0-9]+: )?ERROR:" "$W/t.ll" "$W/emit.err" && st=emit_fail
fi
if [[ $st == ok ]]; then
  bash "$SCRIPTS/instrument_ll.sh" "$W/t.ll" "$W/t.o" 0 2>"$W/inst.err" || st=inst_fail
fi
if [[ $st == ok ]]; then
  clang -fprofile-instr-generate "$W/t.o" "$B/sancov_dump.o" "$B/rt_cov"/*.o \
    -framework Security -framework CoreFoundation -o "$W/t.bin" 2>"$W/link.err" || st=link_fail
fi
if [[ $st == ok ]]; then
  nm -n "$W/t.bin" | awk '$2=="t"||$2=="T"' > "$W/t.nm"
  SPROUT_STDLIB_ROOT="$REPO/stdlib" LLVM_PROFILE_FILE="$R/rtprof/rt-%8m.profraw" \
    SANCOV_OUT="$R/flags" SANCOV_TAG="$idx" SANCOV_PCS="$W/t.pcs" \
    perl -e 'alarm 600; exec @ARGV' "$W/t.bin" > "$W/run.out" 2>&1
  ec=$?
  if [[ $ec -ne 0 ]]; then st="run_exit_$ec"
  elif grep -q "^SUITE FAILED" "$W/run.out"; then st=suite_failed; fi
  # keep the binaries (llvm-cov needs one for the runtime mapping); drop the object
  rm -f "$W/t.o"
fi
printf '%s\t%s\t%s\t%s\n' "$idx" "$kind" "$st" "$f" > "$W/status"
