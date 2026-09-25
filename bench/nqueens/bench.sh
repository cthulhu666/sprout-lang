#!/usr/bin/env bash
# N-Queens benchmark, grouped by DATA REPRESENTATION.
#
# The three representations solve the same problem with different amounts of
# work per node, so a number from one says nothing about a number from another:
# the bitmask variants are ~50x faster than the persistent ones in every
# language that has both. Only compare within a section.
#
# Compiled languages are pre-built so we time execution only, not compilation.
#
# usage: bench.sh [persistent|mutable|bitmask]
#   With no argument every section runs, which takes ~2 minutes and puts the
#   later sections on a machine the earlier ones have been heating for a minute.
#   That is enough to cost the bitmask section ~2x. Name a section to measure
#   one representation on a settled machine.
set -euo pipefail

ONLY="${1:-all}"
case "$ONLY" in
  all|persistent|mutable|bitmask) ;;
  *) echo "usage: $0 [persistent|mutable|bitmask]" >&2; exit 2 ;;
esac

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
mkdir -p "$BIN"

RED='\033[0;31m'; CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
sep() { printf '%0.s─' {1..70}; echo; }

# ── Compile phase ─────────────────────────────────────────────────────────────

echo -e "${BOLD}=== Compiling ===${RESET}"

echo -n "  [Haskell unboxed]  ghc -O2 ... "
ghc -O2 -o "$BIN/nqueens_hs" "$DIR/nqueens.hs" -outputdir "$BIN/hs_obj" 2>/dev/null \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Haskell boxed]    ghc -O2 ... "
ghc -O2 -o "$BIN/nqueens_hs_boxed" "$DIR/nqueens_boxed.hs" -outputdir "$BIN/hs_obj" 2>/dev/null \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Go]       go build  ... "
(cd "$DIR" && go build -o "$BIN/nqueens_go" "$DIR/nqueens.go") \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

# Both Sprout variants go through `just compile-native`, which whole-program
# links (scripts/link_whole_program.sh) — the runtime is merged into the same
# LLVM module, so GC root push/pop inline instead of becoming calls.
build_sprout() { # <source> <output name> <label>
  echo -n "  [Sprout $3] just compile-native ... "
  if [[ ! -x "$REPO/build/compile_driver_bin_stage1" ]]; then
    echo -e "${RED}compile_driver_bin_stage1 not found${RESET}"
    return
  fi
  (cd "$REPO" && just compile-native "$1" "$BIN/$2") 2>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
}
build_sprout examples/nqueens.sprout         nqueens_sprout         "array  "
build_sprout examples/nqueens_bitmask.sprout nqueens_sprout_bitmask "bitmask"

echo
sep

# ── Run phase ─────────────────────────────────────────────────────────────────

echo -e "${BOLD}=== Results ===${RESET}"

# Returns 1 when this section was filtered out, so the caller can skip its
# entries wholesale: `section <key> ... || skip=1`.
want() { [[ "$ONLY" == all || "$ONLY" == "$1" ]]; }

section() { # <key> <title> <blurb>
  want "$1" || return 1
  echo
  sep
  echo -e "${BOLD}$2${RESET}"
  echo -e "${DIM}$3${RESET}"
  echo
}

# Skip silently when an implementation was not built or its interpreter is
# absent — a partial run is still useful, and a missing ghc must not abort it.
entry() { # <label> <command...>
  local label="$1"; shift
  if [[ ! -x "$1" ]] && ! command -v "$1" >/dev/null 2>&1; then
    return 0
  fi
  echo -e "${CYAN}── $label${RESET}"
  "$@"
  echo
}

if section persistent "REPRESENTATION 1: persistent / copy-on-write" \
  "Three boolean constraint arrays, copied on every queen placement, so the
caller still holds the unmodified arrays after the recursive call. O(n)
allocation per step. This is the representation Sprout's Vec forces."
then
  entry "Sprout — Vec Bool (whole-program linked, execution only)" "$BIN/nqueens_sprout"
  entry "Haskell — UArray Int Bool (bit-packed, unboxed)"          "$BIN/nqueens_hs"
  entry "Haskell — Array Int Bool (boxed)"                         "$BIN/nqueens_hs_boxed"
  entry "Go — []bool with copy per placement"                      "$BIN/nqueens_go" pure
  entry "Ruby — Array#dup per placement"                           ruby "$DIR/nqueens_pure.rb"
  entry "Python — list[:] per placement"                           python3 "$DIR/nqueens_pure.py"
fi

if section mutable "REPRESENTATION 2: mutable in-place backtracking" \
  "One set of arrays, written on the way down and unwritten on the way back up.
Zero allocation per step. No Sprout entry yet — stdlib/mutable.sprout's MutVec
could express it, at the cost of making the whole search !{IO}."
then
  entry "Go — []bool mutate/undo" "$BIN/nqueens_go" mutable
  entry "Ruby — mutate/undo"      ruby    "$DIR/nqueens_mut.rb"
  entry "Python — mutate/undo"    python3 "$DIR/nqueens_mut.py"
fi

if section bitmask "REPRESENTATION 3: bitmask" \
  "Constraints packed into three Ints, iterating only the safe columns rather
than testing every column. No arrays, no allocation, and a smaller search
tree — hence ~50x faster than representation 1 in both languages below."
then
  entry "Sprout — Int masks (whole-program linked, execution only)" "$BIN/nqueens_sprout_bitmask"
  entry "Go — int masks"                                            "$BIN/nqueens_go" bitmask
fi

echo
sep
echo -e "${BOLD}Note:${RESET} compare WITHIN a section only — the sections do different work."
echo       "      Compiled times are execution-only (no compilation overhead)."
echo       "      Python/Ruby print their own internal timings, so interpreter"
echo       "      startup (~50 ms) is excluded there too."
if [[ "$ONLY" == all ]]; then
  echo
  echo -e "${BOLD}One sequential pass is indicative, not a measurement.${RESET}"
  echo       "      Later sections run on a hotter machine: the bitmask section costs"
  echo       "      ~2x more here than on a settled one. README.md's tables are medians"
  echo       "      of interleaved runs. To compare one representation, run e.g."
  echo       "      bash bench/nqueens/bench.sh bitmask"
fi
