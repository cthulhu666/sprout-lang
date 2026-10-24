#!/usr/bin/env bash
# A* benchmark: Sprout vs Haskell (mutable IOUArray) vs Go vs Java vs JavaScript
#              vs Python (heapq) vs Ruby (bsearch_index)
# 100×100 grid; each port sets its own run count and reports µs per run.
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

echo -n "  [Haskell]   ghc -O2 ... "
ghc -O2 -o "$BIN/astar_hs" "$DIR/astar.hs" -outputdir "$BIN/hs_obj" 2>/dev/null \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Go]        go build  ... "
(cd "$DIR" && go build -o "$BIN/astar_go" "$DIR/astar.go") \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Java]      javac     ... "
mkdir -p "$BIN/java_cls"
javac -d "$BIN/java_cls" "$DIR/Astar.java" 2>/dev/null \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

build_sprout() { # <source> <output name> <label>
  echo -n "  [Sprout $3] just compile-native ... "
  (cd "$REPO" && just compile-native "$1" "$BIN/$2") 2>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
}

if [[ -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  build_sprout examples/astar.sprout        astar_sprout        "mutvec"
  build_sprout examples/astar_linvec.sprout astar_sprout_linvec "linvec"
else
  echo -e "${RED}compile_driver_bin_stage1 not found — run: just bootstrap-from-seed${RESET}"
fi

echo
sep

# ── Run phase ─────────────────────────────────────────────────────────────────

echo -e "${BOLD}=== Results (A* runs on a 100×100 grid) ===${RESET}"
echo

run_section() {
  local label="$1"; shift
  echo -e "${CYAN}── $label${RESET}"
  "$@"
  echo
}

if [[ -x "$BIN/astar_sprout" ]]; then
  run_section "Sprout (clang -O2, execution only; MutVec, tuple-list open set)" "$BIN/astar_sprout"
fi

if [[ -x "$BIN/astar_sprout_linvec" ]]; then
  run_section "Sprout (clang -O2, execution only; pure, LinVec, linear open list)" "$BIN/astar_sprout_linvec"
fi

if [[ -x "$BIN/astar_hs" ]]; then
  run_section "Haskell (ghc -O2, mutable IOUArray, sorted-list open set)" "$BIN/astar_hs"
fi

if [[ -x "$BIN/astar_go" ]]; then
  run_section "Go (mutable arrays, sorted-slice open set)" "$BIN/astar_go"
fi

if [[ -d "$BIN/java_cls" ]] && command -v java &>/dev/null; then
  echo -e "${CYAN}── Java (JIT warmed up, PriorityQueue open set, mutable arrays)${RESET}"
  java -cp "$BIN/java_cls" Astar
  echo
fi

if command -v node &>/dev/null; then
  echo -e "${CYAN}── JavaScript / Node.js (JIT warmed up, sorted-array open set, TypedArrays)${RESET}"
  node "$DIR/astar.js"
  echo
fi

if command -v python3 &>/dev/null; then
  echo -e "${CYAN}── Python 3 (heapq open set, mutable lists)${RESET}"
  python3 "$DIR/astar.py"
  echo
fi

if command -v ruby &>/dev/null; then
  echo -e "${CYAN}── Ruby (bsearch_index sorted-array open set, mutable arrays)${RESET}"
  ruby "$DIR/astar.rb"
  echo
fi

sep
echo -e "${BOLD}Note:${RESET} Sprout times are execution-only (no compilation overhead)."
echo       "      Sprout/Haskell/Go/JS/Ruby open-set insert is O(n); Java/Python use O(log n) heaps."
echo       "      Go/JS/Ruby insert uses in-place copy (O(1) amortised alloc); Haskell uses linked list."
echo       "      Sprout MutVec writes in place under !{IO}; LinVec writes in place in a pure function."
echo       "      The ports break ties on equal f differently, which changes the work ~20x;"
echo       "      only Sprout and Haskell share a rule. See README.md, Tie-breaks."
