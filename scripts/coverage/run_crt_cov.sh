#!/usr/bin/env bash
# Run tests/c_runtime/run.sh unmodified, with a clang shim that adds coverage flags.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
R="$COV_DIR/crt_run"
rm -rf "$R"; mkdir -p "$R/prof" "$R/bins"
chmod +x "$SCRIPTS/shim/clang"
cd "$REPO"
COV_REAL_CLANG="$(command -v clang)" COV_KEEP_BINS="$R/bins" \
  PATH="$SCRIPTS/shim:$PATH" LLVM_PROFILE_FILE="$R/prof/%p.profraw" \
  bash tests/c_runtime/run.sh > "$R/log" 2>&1
rc=$?
echo "c_runtime run.sh exit: $rc" | tee "$R/status"
exit "$rc"
