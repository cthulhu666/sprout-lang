#!/usr/bin/env bash
# instrument_ll.sh <in.ll> <out.o> <pgo:0|1>
# Sancov (all blocks, no pruning, bool flags, pc table) [+ IR PGO with entry counter], then -O2 to an object.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
in="$1"; out="$2"; pgo="$3"
tmp="${out%.o}.inst.bc"
passes="sancov-module"
extra=(-sanitizer-coverage-level=2 -sanitizer-coverage-inline-bool-flag -sanitizer-coverage-pc-table -sanitizer-coverage-prune-blocks=0)
if [[ "$pgo" == 1 ]]; then passes="$passes,pgo-instr-gen,instrprof"; extra+=(-pgo-instrument-entry); fi
sed -E "1,5s/^target triple = .*/target triple = \"$COV_TRIPLE\"/" "$in" \
  | opt -mtriple="$COV_TRIPLE" -passes="$passes" "${extra[@]}" -o "$tmp"
clang -c -O2 -Wno-override-module "$tmp" -o "$out"
rm -f "$tmp"
