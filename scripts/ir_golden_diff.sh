#!/usr/bin/env bash
# scripts/ir_golden_diff.sh
#
# Re-emit --use-ir-codegen IR for every IR-compilable corpus file and byte-diff
# against the committed goldens in tests/golden/ir/.
#
# Inclusion criteria (same as ir_golden_snapshot.sh):
#   1. Compiler emits non-empty output with no "ERROR:" lines.
#   2. Output passes `opt --passes=verify`.
#
# Exit codes:
#   0  All included files match their goldens exactly.
#   1  One or more differences (missing golden, compile regression, or byte diff).
#
# Flattened-name scheme (must match ir_golden_snapshot.sh):
#   examples/foo.sprout          -> tests/golden/ir/examples__foo.sprout.ll
#   tests/smoke_shapes/01_x.spr  -> tests/golden/ir/smoke_shapes__01_x.spr.ll

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPILER="$REPO_ROOT/build/compile_driver_bin_stage1"
GOLDEN_DIR="$REPO_ROOT/tests/golden/ir"
OPT="opt"

if [[ ! -x "$COMPILER" ]]; then
  echo "ERROR: compiler not found at $COMPILER" >&2
  exit 1
fi

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

differences=0
checked=0

flatten_name() {
  local path="$1"
  local rel
  rel="${path#"$REPO_ROOT/"}"
  rel="${rel#./}"
  local dir_part base_part
  dir_part="$(dirname "$rel")"
  base_part="$(basename "$rel")"
  local flat_dir
  flat_dir="${dir_part//\//__}"
  echo "${flat_dir}__${base_part}.ll"
}

report_diff() {
  local label="$1"
  local file_a="$2"
  local file_b="$3"
  echo "DIFF: $label"
  diff --unified=3 "$file_a" "$file_b" | head -40 || true
  echo "---"
}

process_file() {
  local src="$1"
  local tmp_ir="$TMPDIR_WORK/current.ll"

  "$COMPILER" --use-ir-codegen stdlib "$src" > "$tmp_ir" 2>&1 || true

  local name
  name="$(flatten_name "$src")"
  local golden="$GOLDEN_DIR/$name"

  # Check if output is empty or has ERROR
  if [[ ! -s "$tmp_ir" ]] || grep -q "^ERROR:" "$tmp_ir"; then
    # File was expected to be IR-compilable (golden exists) but now fails
    if [[ -f "$golden" ]]; then
      echo "REGRESSION: $src no longer compiles cleanly (golden exists at $golden)"
      differences=$((differences + 1))
    fi
    # else: was never included — still not included, skip silently
    return
  fi

  # opt verify gate
  if ! "$OPT" --passes=verify "$tmp_ir" -o /dev/null 2>/dev/null; then
    if [[ -f "$golden" ]]; then
      echo "REGRESSION: $src fails opt --passes=verify (golden exists at $golden)"
      differences=$((differences + 1))
    fi
    return
  fi

  # File is IR-compilable — it must have a golden
  checked=$((checked + 1))
  if [[ ! -f "$golden" ]]; then
    echo "MISSING GOLDEN: $src -> expected $golden"
    differences=$((differences + 1))
    return
  fi

  if ! diff -q "$tmp_ir" "$golden" > /dev/null 2>&1; then
    report_diff "$src" "$golden" "$tmp_ir"
    differences=$((differences + 1))
  fi
}

for src in "$REPO_ROOT"/examples/*.sprout "$REPO_ROOT"/tests/smoke_shapes/*.spr; do
  [[ -f "$src" ]] || continue
  process_file "$src"
done

if [[ "$differences" -gt 0 ]]; then
  echo "==> ir-golden-diff: $checked files checked, $differences difference(s) found — FAIL"
  exit 1
else
  echo "==> ir-golden-diff: $checked files, 0 differences"
  exit 0
fi
