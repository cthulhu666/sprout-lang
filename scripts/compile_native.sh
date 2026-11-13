#!/usr/bin/env bash
# Compile a Sprout source to a whole-program-linked native binary with the stage-1 compiler.
# Shared by `just compile-native` and bench/gc_minor/ab.sh, so neither needs the other.
#
# usage: compile_native.sh <src.sprout> <output-binary>
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <src.sprout> <output-binary>" >&2
  exit 2
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Same as the justfile's clang_extra.
case "$(uname -s)" in
  Darwin) CLANG_EXTRA="-framework Security -framework CoreFoundation" ;;
  *) CLANG_EXTRA="" ;;
esac

LL="$(mktemp "${TMPDIR:-/tmp}/sprout_compile_native_XXXXXX")"
trap 'rm -f "$LL"' EXIT
"$REPO/build/compile_driver_bin_stage1" --emit-ir "$REPO/stdlib" --package-root "$REPO" "$1" > "$LL"
# shellcheck disable=SC2086
bash "$REPO/scripts/link_whole_program.sh" "$LL" "$2" $CLANG_EXTRA
