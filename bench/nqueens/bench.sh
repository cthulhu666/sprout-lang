#!/usr/bin/env bash
# N-Queens benchmark: Sprout vs Haskell vs Go (pure/mutable/bitmask) vs Python vs Ruby
# Compiled languages are pre-built so we time execution only, not compilation.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
mkdir -p "$BIN"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
sep() { printf '%0.s─' {1..60}; echo; }

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

echo -n "  [Sprout]   just compile-native ... "
if [[ -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  (cd "$REPO" && just compile-native examples/nqueens.sprout "$BIN/nqueens_sprout") 2>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
else
  echo -e "${RED}compile_driver_bin_stage1 not found${RESET}"
fi

echo
sep

# ── Run phase ─────────────────────────────────────────────────────────────────

echo -e "${BOLD}=== Results ===${RESET}"
echo

run_section() {
  local label="$1"; shift
  echo -e "${CYAN}── $label${RESET}"
  "$@"
  echo
}

# Sprout: execution only (compiled binary, counts N=1,4,8,10,12)
if [[ -x "$BIN/nqueens_sprout" ]]; then
  echo -e "${CYAN}── Sprout (clang -O2, execution only)${RESET}"
  "$BIN/nqueens_sprout"
  echo
fi

# Haskell: both variants
if [[ -x "$BIN/nqueens_hs" ]]; then
  run_section "Haskell unboxed UArray (ghc -O2, execution only)" "$BIN/nqueens_hs"
fi

if [[ -x "$BIN/nqueens_hs_boxed" ]]; then
  run_section "Haskell boxed Array (ghc -O2, execution only)" "$BIN/nqueens_hs_boxed"
fi

# Go: all three variants with internal timing
if [[ -x "$BIN/nqueens_go" ]]; then
  run_section "Go" "$BIN/nqueens_go"
fi

# Python
if command -v python3 &>/dev/null; then
  echo -e "${CYAN}── Python 3 pure (list copy)${RESET}"
  python3 "$DIR/nqueens_pure.py"
  echo
  echo -e "${CYAN}── Python 3 mutable (backtracking)${RESET}"
  python3 "$DIR/nqueens_mut.py"
  echo
fi

# Ruby
if command -v ruby &>/dev/null; then
  echo -e "${CYAN}── Ruby pure (array.dup)${RESET}"
  ruby "$DIR/nqueens_pure.rb"
  echo
  echo -e "${CYAN}── Ruby mutable (backtracking)${RESET}"
  ruby "$DIR/nqueens_mut.rb"
  echo
fi

sep
echo -e "${BOLD}Note:${RESET} Sprout times are execution-only (no compilation overhead)."
echo       "      Python/Ruby times include interpreter startup (~50 ms)."
echo       "      Haskell and Go times are execution-only."
