#!/usr/bin/env bash
# Extracts external signatures from an LLVM .ll file as a normalized,
# diffable canonical form.
#
# Output: one normalized signature per line, sorted.
#
# What is kept:
#   - Every `declare ...` line (external function declarations)
#   - Every `define ...` opening line, with body stripped (exported fns)
#   - Argument types, return types, ABI attributes (`sret`, `byval`, `noalias`, etc.)
#   - Function names (the @<name> identity)
#
# What is normalized away:
#   - Local SSA names (%tmp.N → %_)
#   - Trailing whitespace
#
# What is dropped:
#   - Bodies of `define` blocks (entry: labels, instructions, etc.)
#   - Comments (`;` lines)
#   - target triple / target datalayout
#   - Module-level metadata
#
# Used by: scripts/cpr_differential_check.sh
#
# The premise: ABI mismatches between codegen paths show up as different
# external signatures for the same source. Diff stays small; signal-to-noise
# stays high.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: extract_call_signatures.sh <llvm-ir-file>" >&2
  exit 1
fi

LL="$1"
if [[ ! -f "$LL" ]]; then
  echo "ERROR: $LL not found" >&2
  exit 1
fi

# Extract declare lines (raw signatures) and define openings (strip body at
# the first `{`).  Both forms include the ABI-relevant detail (return type +
# arg types + sret/byval attrs).
{
  grep -E '^declare ' "$LL" || true
  grep -E '^define ' "$LL" | sed -E 's/\{.*$/{/' || true
} \
  | sed -E 's/%[a-zA-Z_][a-zA-Z0-9_.]*/%_/g' \
  | sed -E 's/[[:space:]]+$//' \
  | grep -v '^$' \
  | LC_ALL=C sort -u
