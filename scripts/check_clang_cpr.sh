#!/usr/bin/env bash
# Emitted IR declares the CPR `_unboxed` runtime functions as returning i128. clang
# must lower `unsigned __int128` the same way on this host, or whole-program linking
# keeps every such call un-inlined. x86_64 clang <= 21 spells it `{ i64, i64 }`.
# Probes the lowering rather than the version: Apple's version numbers are not
# LLVM's. See docs/gates.md §CPR declares.
set -euo pipefail

CLANG="${1:-clang}"
TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_clangcpr_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

printf 'unsigned __int128 f(unsigned long long a) { return a; }\n' > "$TMPD/p.c"
"$CLANG" -O2 -S -emit-llvm "$TMPD/p.c" -o "$TMPD/p.ll"

if ! grep -Eq '^define .*\bi128 @f\(' "$TMPD/p.ll"; then
  got="$(grep -E '^define .*@f\(' "$TMPD/p.ll" || true)"
  echo "error: $("$CLANG" --version | head -1) lowers __int128 returns as:" >&2
  echo "  $got" >&2
  echo "Sprout needs one that returns i128. On x86_64 that is clang >= 22" >&2
  echo "(https://apt.llvm.org); put its bin directory, e.g. /usr/lib/llvm-22/bin, first on PATH." >&2
  exit 1
fi
