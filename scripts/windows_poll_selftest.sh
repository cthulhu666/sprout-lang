#!/usr/bin/env bash
# Build and RUN the WSAPoll poller self-test (tests/windows/poll_selftest.c).
#
# The only Windows gate that checks behaviour rather than compilation, and the only one that can
# before W5: the poller needs Ws2_32 and nothing else — no scheduler, no GC, no Sprout runtime.
# See the test's own header for why the poller is where compile-only gating stops being enough.
#
# Windows host ONLY. Off-Windows this exits 0 with a note rather than failing, so `just gate` on a
# developer's machine is not red for a reason no one can act on; `scripts/windows_tu_check.sh`
# already covers the cross-compile half of the story there.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/tests/windows/poll_selftest.c"

case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT) ;;
  *)
    echo "[windows-poll-selftest] skipped: needs a Windows host to run the binary."
    echo "  Cross-compilation of this TU is covered by scripts/windows_tu_check.sh."
    exit 0
    ;;
esac

# Same discovery as windows_ir_gate.sh: clang is not on PATH by default on the GitHub image.
CLANG="${SPROUT_CLANG:-}"
if [ -z "$CLANG" ]; then
  if command -v clang >/dev/null 2>&1; then
    CLANG="clang"
  elif [ -x "/c/Program Files/LLVM/bin/clang.exe" ]; then
    CLANG="/c/Program Files/LLVM/bin/clang.exe"
  else
    echo "[windows-poll-selftest] no clang found. Set SPROUT_CLANG to one." >&2
    exit 1
  fi
fi

echo "==> clang: $CLANG"
"$CLANG" --version | head -1
echo

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
EXE="$TMP/poll_selftest.exe"

# MSVC target: the ship surface (§4.1). -Wall -Wextra because this compiles the backend itself,
# so a warning here is a warning in shipped runtime code.
"$CLANG" --target=x86_64-pc-windows-msvc -Wall -Wextra -O1 "$SRC" -o "$EXE" -lws2_32

# A hang is a real failure mode for this component — an unclamped WSAPoll timeout turns an
# already-due deadline into an indefinite wait — so bound it rather than letting CI's job timeout
# report it six minutes later as an unexplained kill.
if command -v timeout >/dev/null 2>&1; then
  timeout 60 "$EXE"
else
  "$EXE"
fi
