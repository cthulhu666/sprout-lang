#!/usr/bin/env bash
# scripts/ir_byte_identical_check.sh
#
# Byte-identical IR gate for behavior-preserving compiler refactors (e.g. the
# dict-resolution north-star increments).  Compiles every corpus file with TWO
# compilers via the SAME --emit-ir path and diffs the IR text.
#
#   OLD = build/compile_driver_bin_stage1  (built from the committed seed)
#   NEW = build/compile_driver_bin_stage2  (stage1 compiling the working tree)
#
# On a byte-identical refactor the two emit identical IR for every file.  Any
# diff is a regression.  Corpus: examples/*.sprout + tests/stdlib/*.spr.
#
# Exit 0 iff every file that OLD compiles is byte-identical under NEW.
#
# Usage: bash scripts/ir_byte_identical_check.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLD="$REPO_ROOT/build/compile_driver_bin_stage1"
NEW="$REPO_ROOT/build/compile_driver_bin_stage2"

for bin in "$OLD" "$NEW"; do
  if [[ ! -x "$bin" ]]; then
    echo "ERROR: compiler not found at $bin" >&2
    exit 1
  fi
done

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

n_total=0
n_match=0
n_diff=0
n_skip=0
declare -a DIFFS=()

for src in \
    "$REPO_ROOT"/examples/*.sprout \
    "$REPO_ROOT"/tests/stdlib/*.spr \
    "$REPO_ROOT"/tests/stdlib/compiler/*.spr; do
  [[ -f "$src" ]] || continue
  label="${src#$REPO_ROOT/}"
  old_ir="$SCRATCH/old.ll"
  new_ir="$SCRATCH/new.ll"

  "$OLD" --emit-ir stdlib --package-root "$REPO_ROOT" "$src" >"$old_ir" 2>/dev/null || true
  # OLD cannot compile this file (or emits an error) → not a byte-identity case.
  if [[ ! -s "$old_ir" ]] || grep -q "^ERROR:" "$old_ir" 2>/dev/null; then
    n_skip=$((n_skip + 1))
    continue
  fi

  n_total=$((n_total + 1))
  "$NEW" --emit-ir stdlib --package-root "$REPO_ROOT" "$src" >"$new_ir" 2>/dev/null || true

  if cmp -s "$old_ir" "$new_ir"; then
    n_match=$((n_match + 1))
  else
    n_diff=$((n_diff + 1))
    DIFFS+=("$label")
  fi
done

echo "==> ir-byte-identical: $n_total compared, $n_match identical, $n_diff DIFFER, $n_skip skipped (old-uncompilable)"
if [[ $n_diff -gt 0 ]]; then
  echo "--- files with IR divergence ---"
  for d in "${DIFFS[@]}"; do echo "  $d"; done
  exit 1
fi
exit 0
