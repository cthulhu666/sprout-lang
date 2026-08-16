#!/usr/bin/env bash
# Windows port: compile the runtime translation units that are EXPECTED to build for Windows.
#
# The list below grows one entry per milestone (docs/windows-port-v0.md §5). That is the point:
# a TU on the list is a promise, so a regression that stops it compiling turns this red, while a
# TU not yet on the list is honestly reported as outstanding rather than silently skipped.
#
# Two toolchains, deliberately, matching §4.1's develop-mingw / ship-MSVC rule:
#   * on a Windows host  — clang targeting x86_64-pc-windows-msvc, using the installed SDK.
#     This is the SHIP surface, and the stricter of the two: MSVC has no unistd.h, no
#     sys/time.h, no POSIX shims. CI runs this one.
#   * elsewhere          — clang targeting x86_64-w64-mingw32 against a mingw-w64 sysroot.
#     The developer loop on macOS/Linux, which needs no Windows machine.
#
# -fsyntax-only: there is nothing to link against until W5, so this measures compilation only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# One entry per TU that must compile, with the milestone that put it here.
EXPECTED=(
  "runtime/sprout_context.h|W1 — the fiber arm (compiled via a TU that includes it)"
  # The whole scheduler TU, not just the seam: sprout_scheduler.h only DECLARES the poller
  # interface, so nothing on this path reaches a POSIX poller header.
  "runtime/sprout_scheduler.c|W1 — the whole scheduler TU"
)
# Not yet expected; listed so the output is a work list rather than a silence.
OUTSTANDING=(
  "runtime/sprout_poll.c|W2 — needs the WSAPoll backend; stops on sys/epoll.h"
  "runtime/sprout_runtime.c|W3 — stops on regex.h, its first non-standard include"
)

CLANG="${SPROUT_CLANG:-clang}"
command -v "$CLANG" >/dev/null 2>&1 || { echo "[windows-tu-check] no clang ($CLANG)." >&2; exit 1; }

case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    TRIPLE="x86_64-pc-windows-msvc"
    SYSROOT_ARGS=()
    MODE="MSVC (ship surface, host SDK)"
    ;;
  *)
    TRIPLE="x86_64-w64-mingw32"
    SYSROOT="${SPROUT_MINGW_SYSROOT:-}"
    if [ -z "$SYSROOT" ] && prefix="$(brew --prefix mingw-w64 2>/dev/null)"; then
      SYSROOT="$prefix/toolchain-x86_64/x86_64-w64-mingw32"
    fi
    if [ -z "$SYSROOT" ] || [ ! -d "$SYSROOT" ]; then
      echo "[windows-tu-check] no mingw-w64 sysroot. Install it (brew install mingw-w64) or set" >&2
      echo "  SPROUT_MINGW_SYSROOT. Looked at: ${SYSROOT:-<unset>}" >&2
      exit 1
    fi
    SYSROOT_ARGS=(--sysroot="$SYSROOT")
    MODE="mingw-w64 (developer loop)"
    ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> $MODE — $TRIPLE"
"$CLANG" --version | head -1
echo

fail=0
for entry in "${EXPECTED[@]}"; do
  path="${entry%%|*}"; why="${entry#*|}"
  src="$ROOT/$path"
  # A header is checked by compiling a TU that includes it, so the check covers what a real
  # consumer sees rather than the header in isolation.
  if [ "${path##*.}" = "h" ]; then
    printf '#include "%s"\nint main(void){return 0;}\n' "$src" > "$TMP/tu.c"
    target="$TMP/tu.c"
  else
    target="$src"
  fi
  printf '  %-34s ' "$(basename "$path")"
  if "$CLANG" --target="$TRIPLE" "${SYSROOT_ARGS[@]}" -fsyntax-only -Wall -Wextra \
       "$target" 2>"$TMP/err"; then
    echo "ok      ($why)"
  else
    echo "FAILED  ($why)"
    sed 's/^/      /' "$TMP/err" | head -12
    fail=$((fail + 1))
  fi
done

echo
echo "  still outstanding (not a failure — these are the remaining milestones):"
for entry in "${OUTSTANDING[@]}"; do
  path="${entry%%|*}"; why="${entry#*|}"
  printf '    %-30s %s\n' "$(basename "$path")" "$why"
done

echo
if [ "$fail" -ne 0 ]; then
  echo "==> windows-tu-check FAILED ($fail of ${#EXPECTED[@]})" >&2
  echo "    Something that used to compile for Windows no longer does. Do not shrink the" >&2
  echo "    EXPECTED list to make this pass — that is the one way this gate can be defeated." >&2
  exit 1
fi
echo "==> windows-tu-check ✓ (${#EXPECTED[@]} expected, ${#OUTSTANDING[@]} outstanding)"
