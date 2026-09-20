#!/usr/bin/env bash
# Link emitted IR against the runtime as ONE LLVM module, so runtime calls can be
# inlined into Sprout code.  Measured -38.1% on bench/gc_roots through
# `just compile-native`; -38.7% elsewhere is the internalize experiment, which
# is not what this script does.
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

STAMP="$CACHE/runtime.stamp"

mkdir -p "$CACHE"

# What the cache was built from: every runtime source's CONTENT, plus this
# script's, sorted so glob order cannot matter.  Content rather than mtime
# because mtime answers "is anything newer", which says nothing when a source
# is deleted or when this script's own compile flags change.
stamp_of() {
  {
    for src in "$ROOT"/runtime/*.c "$ROOT"/runtime/*.h "${BASH_SOURCE[0]}"; do
      [ -e "$src" ] || continue
      printf '%s ' "$src"
      cksum < "$src"
    done
  } | sort
}

# Computed BEFORE the build and written after, so a source edited mid-build
# leaves a stamp that no longer matches and the next run rebuilds.
want="$(stamp_of)"
have=""
[ -f "$STAMP" ] && have="$(cat "$STAMP")"

if [ ! -f "$MERGED" ] || [ "$want" != "$have" ]; then
  # Built in a private directory and moved into place, never written where the
  # next run would read it.  Two concurrent builds then race only on which
  # complete module wins, instead of one reading the other's half-written file
  # -- which the staleness check would afterwards accept as good forever.
  WORK="$CACHE/.work.$$"
  rm -rf "$WORK"
  mkdir -p "$WORK"
  trap 'rm -rf "$WORK"' EXIT

  parts=()
  for src in "$ROOT"/runtime/*.c; do
    base="$(basename "$src" .c)"
    clang -O2 -emit-llvm -S "$src" -o "$WORK/$base.ll"
    sed -E 's/"target-cpu"="[^"]*"//g; s/"target-features"="[^"]*"//g; s/"tune-cpu"="[^"]*"//g' \
      "$WORK/$base.ll" > "$WORK/$base.nf.ll"
    llvm-as "$WORK/$base.nf.ll" -o "$WORK/$base.bc"
    parts+=("$WORK/$base.bc")
  done
  llvm-link "${parts[@]}" -o "$WORK/runtime.bc"

  # Same filesystem as the cache, so both moves are atomic.  The stamp lands
  # last: a crash between them leaves a good module with no stamp, which costs
  # one rebuild, while the reverse would claim a module that is not there.
  mv -f "$WORK/runtime.bc" "$MERGED"
  printf '%s\n' "$want" > "$WORK/stamp"
  mv -f "$WORK/stamp" "$STAMP"

  rm -rf "$WORK"
  trap - EXIT
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_wpl_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

llvm-as "$LL" -o "$TMPD/prog.bc"

# The emitted module is target-neutral and the runtime's is not; llvm-link warns
# and keeps the concrete triple, which is what we want.  Only THAT line is
# dropped: discarding the whole stream also hid duplicate-symbol and unreadable-
# bitcode errors, and under `set -e` the build then died printing nothing.
if ! llvm-link "$TMPD/prog.bc" "$MERGED" -o "$TMPD/merged.bc" 2>"$TMPD/link.err"; then
  cat "$TMPD/link.err" >&2
  echo "link_whole_program: llvm-link failed merging $LL with $MERGED" >&2
  exit 1
fi
grep -v 'different target triples' "$TMPD/link.err" >&2 || true

clang "$TMPD/merged.bc" -O2 "$@" -o "$OUT"
