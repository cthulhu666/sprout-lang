#!/usr/bin/env bash
# Digit-recognizer benchmark: Sprout vs Go vs Java vs Python (plain, no ML libs).
#
# Every implementation trains the SAME model — a 64->24->10 softsign MLP, MSE loss,
# stochastic gradient descent, 25 epochs over 500 samples — with the same
# deterministic LCG weight init, so all reach the same final accuracy (89.33%).
#
# Compiled languages are pre-built so the run phase times execution, not compilation.
# The recognizer is a single training run; compute dominates (data load + process
# startup are sub-1% of wall-clock), so we time the whole run externally rather
# than instrumenting each program.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
BIN="$DIR/bin"
mkdir -p "$BIN"

# Scala 3 needs both scala3-library and the scala 2.13 stdlib on the classpath. Derive
# them from the scalac install (resolve the symlink, glob its libexec) so we can run the
# compiled classes with plain `java -cp`, matching how the Java port is timed. Empty if
# scalac is absent, in which case the Scala ports are skipped.
resolve_link() {
  local p="$1"; cd "$(dirname "$p")"; local t="$(basename "$p")"
  while [ -L "$t" ]; do local l; l="$(readlink "$t")"; cd "$(dirname "$l")"; t="$(basename "$l")"; done
  echo "$(pwd -P)/$t"
}
SCALA_LIBS=""
if command -v scalac &>/dev/null; then
  SCALA_HOME="$(dirname "$(dirname "$(resolve_link "$(command -v scalac)")")")"
  SCALA_LIBS="$(find "$SCALA_HOME/libexec" -name 'scala*library*.jar' 2>/dev/null | tr '\n' ':')"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
sep() { printf '%0.s─' {1..64}; echo; }

# ── Compile phase ─────────────────────────────────────────────────────────────
echo -e "${BOLD}=== Compiling ===${RESET}"

echo -n "  [Go]      go build ... "
(cd "$DIR" && go build -o "$BIN/recognizer_go" "$DIR/recognizer.go") \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Java]    javac ... "
javac -d "$BIN/java_cls" "$DIR/Recognizer.java" 2>/dev/null \
  && echo "done" || echo -e "${RED}FAILED${RESET}"

echo -n "  [Scala]   scalac ... "
if command -v scalac &>/dev/null; then
  mkdir -p "$BIN/scala_cls" # scala3 scalac requires the -d target dir to already exist
  scalac -d "$BIN/scala_cls" "$DIR/Recognizer.scala" "$DIR/RecognizerIdiomatic.scala" 2>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
else
  echo -e "${RED}scalac not found${RESET}"
fi

echo -n "  [Haskell] ghc -O2 ... "
if command -v ghc &>/dev/null; then
  ghc -O2 -outputdir "$BIN/hs_pure"   -o "$BIN/recognizer_hs_pure"   "$DIR/Recognizer.hs"       &>/dev/null \
    && ghc -O2 -outputdir "$BIN/hs_unsafe" -o "$BIN/recognizer_hs_unsafe" "$DIR/RecognizerUnsafe.hs" &>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
else
  echo -e "${RED}ghc not found${RESET}"
fi

echo -n "  [Sprout]  just compile-native ... "
if [[ -x "$REPO/build/compile_driver_bin_stage1" ]]; then
  (cd "$REPO" && mise exec -- just compile-native examples/digit_recognizer/recognizer.sprout "$BIN/recognizer_sprout") 2>/dev/null \
    && echo "done" || echo -e "${RED}FAILED${RESET}"
else
  echo -e "${RED}compile_driver_bin_stage1 not found (run: just build-stage1)${RESET}"
fi

echo
sep

# ── Run phase ─────────────────────────────────────────────────────────────────
# Each program reads examples/digit_recognizer/optdigits_*.txt (repo-relative), so
# runs happen from the repo root. We capture wall-clock via /usr/bin/time -p and
# the program's own "final test accuracy" line.
echo -e "${BOLD}=== Results (full run: 25 epochs SGD over 500 samples) ===${RESET}"
echo
printf "  %-22s %10s   %s\n" "implementation" "wall (s)" "final accuracy"
printf "  %-22s %10s   %s\n" "----------------------" "--------" "--------------"

run_timed() {
  local label="$1"; shift
  local out real acc
  out=$( cd "$REPO" && { /usr/bin/time -p "$@" ; } 2>&1 )
  real=$(echo "$out" | awk '/^real/{print $2}')
  acc=$(echo "$out" | grep -i 'final test accuracy' | head -1)
  [[ -z "$real" ]] && real="—"
  [[ -z "$acc"  ]] && acc="(no accuracy line)"
  printf "  ${CYAN}%-22s${RESET} %10s   %s\n" "$label" "$real" "$acc"
}

[[ -x "$BIN/recognizer_go"     ]] && run_timed "Go"            "$BIN/recognizer_go"
[[ -d "$BIN/java_cls"          ]] && command -v java  &>/dev/null && run_timed "Java"          java -cp "$BIN/java_cls" Recognizer
if [[ -d "$BIN/scala_cls" && -n "$SCALA_LIBS" ]] && command -v java &>/dev/null; then
  run_timed "Scala (imperative)" java -cp "$BIN/scala_cls:$SCALA_LIBS" Recognizer
  run_timed "Scala (idiomatic)"  java -cp "$BIN/scala_cls:$SCALA_LIBS" RecognizerIdiomatic
fi
[[ -x "$BIN/recognizer_hs_unsafe" ]] && run_timed "Haskell (unsafe)" "$BIN/recognizer_hs_unsafe"
[[ -x "$BIN/recognizer_hs_pure"   ]] && run_timed "Haskell (pure)"   "$BIN/recognizer_hs_pure"
[[ -x "$BIN/recognizer_sprout" ]] && run_timed "Sprout (clang -O2)" "$BIN/recognizer_sprout"
command -v python3 &>/dev/null && run_timed "Python (plain)"     python3 "$DIR/recognizer_plain.py"

echo
sep
echo -e "${BOLD}Notes:${RESET}"
echo "  • All hand-written versions reach the SAME accuracy (identical algorithm + seed)."
echo "  • Scala ships two ports: 'imperative' (mutable Array + while, apples-to-apples with"
echo "    Java) and 'idiomatic' (immutable Vector, foldLeft, case-class Net) — the delta is"
echo "    the cost of FP purity + boxed collections at identical accuracy."
echo "  • Haskell ships two ports too: 'pure' (immutable lists, foldl', a fresh Net per step)"
echo "    and 'unsafe' (flat unboxed IOUArray mutated via unsafeRead/unsafeWrite) — same"
echo "    span from maximal purity to hand-tuned mutation, same accuracy."
echo "  • Python (plain) has no numpy; it is pure-interpreter and expected to be slow."
echo "  • recognizer_sklearn.py (library version) is provided for reference but not timed"
echo "    here — it needs numpy + scikit-learn and does not run the same hand-SGD algorithm."
