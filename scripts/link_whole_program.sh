#!/usr/bin/env bash
# Link emitted IR against the runtime as ONE LLVM module, so runtime calls can be
# inlined into Sprout code.  Measured -38.7% on bench/gc_roots.
#
# Why this is not the default everywhere: it costs ~700 ms more per binary than a
# cached .o link, because the whole runtime is re-optimised and codegenned into
# each output.  That pays for a binary that runs for more than ~2 s and is pure
# loss for the 416 one-shot binaries `just test` links.  Reserved for recipes that
# produce long-running binaries.  Rationale and numbers: docs/cross-tu-inlining-v0.md.
#
# Two details are load-bearing:
#   * the runtime's per-function "target-cpu"/"target-features" are stripped, because
#     emitted IR has none and LLVM refuses to inline a callee whose features are not
#     a subset of the caller's.  Emitted IR stays target-neutral (the seed is
#     committed and cross-platform); only the runtime side is rewritten, here.
#   * NO -mcpu is passed.  The clang driver selects the host CPU itself, so each
#     platform keeps the default it has today and a released binary stays as
#     portable as it is now.
#
# usage: link_whole_program.sh <emitted.ll> <output-binary> [extra clang args...]
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <emitted.ll> <output-binary> [extra clang args...]" >&2
  exit 2
fi

LL="$1"; OUT="$2"; shift 2
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/build/runtime-bc"
MERGED="$CACHE/runtime.bc"

mkdir -p "$CACHE"

# Rebuild the cached runtime bitcode when any runtime source is newer than it.
stale=0
[ -f "$MERGED" ] || stale=1
if [ "$stale" -eq 0 ]; then
  for src in "$ROOT"/runtime/*.c "$ROOT"/runtime/*.h; do
    [ -e "$src" ] || continue
    [ "$MERGED" -nt "$src" ] || { stale=1; break; }
  done
fi

if [ "$stale" -eq 1 ]; then
  parts=()
  for src in "$ROOT"/runtime/*.c; do
    base="$(basename "$src" .c)"
    clang -O2 -emit-llvm -S "$src" -o "$CACHE/$base.ll"
    sed -E 's/"target-cpu"="[^"]*"//g; s/"target-features"="[^"]*"//g; s/"tune-cpu"="[^"]*"//g' \
      "$CACHE/$base.ll" > "$CACHE/$base.nf.ll"
    llvm-as "$CACHE/$base.nf.ll" -o "$CACHE/$base.bc"
    parts+=("$CACHE/$base.bc")
  done
  llvm-link "${parts[@]}" -o "$MERGED"
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_wpl_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

llvm-as "$LL" -o "$TMPD/prog.bc"
# The emitted module is target-neutral and the runtime's is not; llvm-link warns
# and keeps the concrete triple, which is what we want.
llvm-link "$TMPD/prog.bc" "$MERGED" -o "$TMPD/merged.bc" 2>/dev/null
clang "$TMPD/merged.bc" -O2 "$@" -o "$OUT"
