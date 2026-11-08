#!/usr/bin/env bash
# Run the full `just test` with the instrumented compiler standing in for build/compile_driver_bin_stage1.
# build/ is gitignored. The original binary is restored on ANY exit (trap) and byte-compared.
# A backup left by a killed run (SIGKILL cannot be trapped) blocks the next start.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
R="$COV_DIR/compiler_run"
cd "$REPO"

if [[ -e "$STAGE1_BACKUP" ]]; then
  echo "ERROR: $STAGE1_BACKUP exists: a previous coverage run did not restore stage-1." >&2
  echo "       Restore it:  cp -p '$STAGE1_BACKUP' '$STAGE1' && rm '$STAGE1_BACKUP'" >&2
  echo "       or rebuild:  rm '$STAGE1_BACKUP' '$STAGE1' && mise exec -- just bootstrap-from-seed" >&2
  exit 1
fi
[[ -x "$STAGE1" ]] || { echo "ERROR: $STAGE1 missing; run: mise exec -- just bootstrap-from-seed" >&2; exit 1; }
[[ -x "$B/compile_driver_inst" ]] || { echo "ERROR: run build_compiler.sh first" >&2; exit 1; }

rm -rf "$R"; mkdir -p "$R/prof" "$R/flags"
rm -f "$B/compiler.pcs"

restore() {
  trap - EXIT
  if [[ -e "$STAGE1_BACKUP" ]]; then
    cp -p "$STAGE1_BACKUP" "$STAGE1" && touch "$STAGE1"
    if cmp -s "$STAGE1_BACKUP" "$STAGE1"; then
      rm -f "$STAGE1_BACKUP"; echo "restored original stage-1 (byte-identical)"
    else
      echo "ERROR: restored stage-1 differs from the backup; backup kept at $STAGE1_BACKUP" >&2
      exit 1
    fi
  fi
}
trap restore EXIT
trap 'exit 130' INT TERM

cp -p "$STAGE1" "$STAGE1_BACKUP"
cp "$B/compile_driver_inst" "$STAGE1"; touch "$STAGE1"
export LLVM_PROFILE_FILE="$R/prof/cc-%8m.profraw"
export SANCOV_OUT="$R/flags" SANCOV_TAG=cc SANCOV_PCS="$B/compiler.pcs"
start=$(date +%s)
mise exec -- just test > "$R/just_test.log" 2>&1
rc=$?
echo "just test exit: $rc  ($(( $(date +%s) - start ))s)" | tee "$R/status"
exit "$rc"
