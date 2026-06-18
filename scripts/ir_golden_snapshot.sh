#!/usr/bin/env bash
# scripts/ir_golden_snapshot.sh
#
# Snapshot --use-ir-codegen output for every IR-compilable corpus file into
# tests/golden/ir/.  A file is included only if:
#   1. The compiler emits non-empty output.
#   2. No output line starts with "ERROR:".
#   3. The output passes `opt --passes=verify`.
#
# Flattened-name scheme:
#   examples/foo.sprout          -> tests/golden/ir/examples__foo.sprout.ll
#   tests/smoke_shapes/01_x.spr  -> tests/golden/ir/smoke_shapes__01_x.spr.ll
# (Drop leading dir segment, replace all remaining "/" with "__", append ".ll")

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

mkdir -p "$GOLDEN_DIR"

snapshotted=0
skipped=0

flatten_name() {
  local path="$1"
  # Remove leading "examples/" or "tests/smoke_shapes/" prefix segment
  # then join remaining parts with "__"
  local rel
  rel="${path#"$REPO_ROOT/"}"    # strip repo root if absolute
  rel="${rel#./}"                 # strip leading ./
  # Extract the meaningful suffix: drop the first path component
  local dir_part base_part
  dir_part="$(dirname "$rel")"
  base_part="$(basename "$rel")"
  # Replace remaining "/" in dir_part with "__" and combine
  local flat_dir
  flat_dir="${dir_part//\//__}"
  echo "${flat_dir}__${base_part}.ll"
}

process_file() {
  local src="$1"
  local tmp_ir="$TMPDIR_WORK/out.ll"

  "$COMPILER" --use-ir-codegen stdlib "$src" > "$tmp_ir" 2>&1 || true

  # Inclusion criteria
  if [[ ! -s "$tmp_ir" ]]; then
    return 1
  fi
  if grep -q "^ERROR:" "$tmp_ir"; then
    return 1
  fi
  if ! "$OPT" --passes=verify "$tmp_ir" -o /dev/null 2>/dev/null; then
    return 1
  fi

  local name
  name="$(flatten_name "$src")"
  cp "$tmp_ir" "$GOLDEN_DIR/$name"
  return 0
}

for src in "$REPO_ROOT"/examples/*.sprout "$REPO_ROOT"/tests/smoke_shapes/*.spr; do
  [[ -f "$src" ]] || continue
  if process_file "$src"; then
    snapshotted=$((snapshotted + 1))
  else
    skipped=$((skipped + 1))
  fi
done

echo "==> ir-golden-snapshot: $snapshotted files snapshotted, $skipped skipped"
