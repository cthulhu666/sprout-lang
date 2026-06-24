#!/usr/bin/env bash
# scripts/ir_runtime_parity.sh
#
# Dual-path run-output parity harness.
#
# Compiles each corpus file via BOTH codegen paths, links+runs both, and diffs
# stdout+exit-code. Catches typed-codegen RUNTIME miscompiles that IR-golden and
# opt-verify checks cannot: valid-but-wrong IR that produces different observable
# output.
#
# Corpus: examples/*.sprout  tests/smoke_shapes/*.spr  tests/stdlib/*.spr
#
# Classification of each file:
#   SKIP           — direct path cannot compile/link or direct binary non-terminating
#   OK             — typed run output matches direct run output exactly
#   TYPED-COMPILE  — typed emit empty / ERROR: / opt-verify fail
#   TYPED-LINK     — typed IR fails to link
#   TYPED-RUNTIME  — typed runs but stdout or exit-code differs from direct
#
# Exit: 0 if all runnable files are OK; nonzero if any TYPED-* among runnables.
#
# Usage: bash scripts/ir_runtime_parity.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPILER="$REPO_ROOT/build/compile_driver_bin_stage1"
RUNTIME="$REPO_ROOT/runtime/sprout_runtime.c"

if [[ ! -x "$COMPILER" ]]; then
  echo "ERROR: compiler not found at $COMPILER" >&2
  exit 1
fi

if [[ ! -f "$RUNTIME" ]]; then
  echo "ERROR: runtime not found at $RUNTIME" >&2
  exit 1
fi

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# Pick timeout command (confirmed available via mise on this system)
TIMEOUT_CMD="timeout"
if command -v mise >/dev/null 2>&1; then
  TIMEOUT_CMD="mise exec -- timeout"
fi

OPT_CMD="opt"
if command -v mise >/dev/null 2>&1; then
  OPT_CMD="mise exec -- opt"
fi

CLANG_CMD="clang"
if command -v mise >/dev/null 2>&1; then
  CLANG_CMD="mise exec -- clang"
fi

FRAMEWORKS="-framework Security -framework CoreFoundation"

# Counters
n_runnable=0
n_ok=0
n_typed_runtime=0
n_typed_compile=0
n_typed_link=0
n_skipped=0

# Arrays for summary output
declare -a RESULTS=()

# --------------------------------------------------------------------------
# link_ir <ir_file> <binary_out>
# Returns 0 on success, 1 on failure.
# --------------------------------------------------------------------------
link_ir() {
  local ir="$1"
  local out="$2"
  $CLANG_CMD -x ir "$ir" -x none "$RUNTIME" $FRAMEWORKS -o "$out" \
    -Wno-override-module 2>/dev/null
}

# --------------------------------------------------------------------------
# run_bin <binary> <stdout_file>
# Runs with a 10s timeout and no stdin. Returns actual exit code (or 124 for
# timeout). Nonzero does NOT abort the script (we capture it explicitly).
# --------------------------------------------------------------------------
run_bin() {
  local bin="$1"
  local out="$2"
  local rc=0
  $TIMEOUT_CMD 10 "$bin" </dev/null >"$out" 2>/dev/null || rc=$?
  echo "$rc"
}

# --------------------------------------------------------------------------
# process_file <src>
# --------------------------------------------------------------------------
process_file() {
  local src="$1"
  local label
  label="$(basename "$src")"

  local direct_ir="$SCRATCH/direct.ll"
  local typed_ir="$SCRATCH/typed.ll"
  local direct_bin="$SCRATCH/direct_bin"
  local typed_bin="$SCRATCH/typed_bin"
  local direct_out="$SCRATCH/direct.out"
  local typed_out="$SCRATCH/typed.out"

  # Cleanup between files
  rm -f "$direct_ir" "$typed_ir" "$direct_bin" "$typed_bin" "$direct_out" "$typed_out"

  # --- STEP 1: direct compile ---
  "$COMPILER" --emit-ir stdlib "$src" >"$direct_ir" 2>/dev/null || true

  if [[ ! -s "$direct_ir" ]] || grep -q "^ERROR:" "$direct_ir" 2>/dev/null; then
    n_skipped=$((n_skipped + 1))
    RESULTS+=("SKIP  [direct-uncompilable]  $label")
    return
  fi

  if ! $OPT_CMD --passes=verify "$direct_ir" -o /dev/null 2>/dev/null; then
    n_skipped=$((n_skipped + 1))
    RESULTS+=("SKIP  [direct-opt-fail]      $label")
    return
  fi

  # --- STEP 2: direct link ---
  if ! link_ir "$direct_ir" "$direct_bin"; then
    n_skipped=$((n_skipped + 1))
    RESULTS+=("SKIP  [direct-link-fail]     $label")
    return
  fi

  # --- STEP 3: direct run (termination check) ---
  local direct_rc
  direct_rc="$(run_bin "$direct_bin" "$direct_out")"

  if [[ "$direct_rc" -eq 124 ]]; then
    # Timeout — non-terminating binary
    n_skipped=$((n_skipped + 1))
    RESULTS+=("SKIP  [direct-timeout]       $label")
    return
  fi

  if [[ "$direct_rc" -ne 0 ]]; then
    # Crashes or errors on the direct path — not our concern
    n_skipped=$((n_skipped + 1))
    RESULTS+=("SKIP  [direct-nonzero:$direct_rc]  $label")
    return
  fi

  # File is RUNNABLE — now test the typed path
  n_runnable=$((n_runnable + 1))

  # --- STEP 4: typed compile ---
  "$COMPILER" --use-ir-codegen stdlib "$src" >"$typed_ir" 2>/dev/null || true

  if [[ ! -s "$typed_ir" ]] || grep -q "^ERROR:" "$typed_ir" 2>/dev/null; then
    n_typed_compile=$((n_typed_compile + 1))
    local direct_head
    direct_head="$(head -3 "$direct_out" 2>/dev/null || true)"
    RESULTS+=("TYPED-COMPILE  $label")
    RESULTS+=("  direct (first 3 lines): $direct_head")
    RESULTS+=("  typed: (empty/ERROR emit)")
    return
  fi

  if ! $OPT_CMD --passes=verify "$typed_ir" -o /dev/null 2>/dev/null; then
    n_typed_compile=$((n_typed_compile + 1))
    local direct_head
    direct_head="$(head -3 "$direct_out" 2>/dev/null || true)"
    RESULTS+=("TYPED-COMPILE  $label  (opt-verify fail)")
    RESULTS+=("  direct (first 3 lines): $direct_head")
    RESULTS+=("  typed: (invalid IR)")
    return
  fi

  # --- STEP 5: typed link ---
  if ! link_ir "$typed_ir" "$typed_bin"; then
    n_typed_link=$((n_typed_link + 1))
    local direct_head
    direct_head="$(head -3 "$direct_out" 2>/dev/null || true)"
    RESULTS+=("TYPED-LINK     $label")
    RESULTS+=("  direct (first 3 lines): $direct_head")
    RESULTS+=("  typed: (link failed)")
    return
  fi

  # --- STEP 6: typed run ---
  local typed_rc
  typed_rc="$(run_bin "$typed_bin" "$typed_out")"

  # Golden mode: for ABI-divergent constructs (tuples/closures) the direct path
  # is NOT a valid reference — e.g. direct silently drops `print(tuple)` while
  # typed renders it structurally.  When a golden file exists we assert the
  # typed output against it directly (a stronger check than typed==direct), which
  # is the gate's intended post-flip form once direct codegen is retired.
  local golden="$REPO_ROOT/tests/golden/runtime/$label.out"
  if [[ -f "$golden" ]]; then
    if [[ "$typed_rc" -eq 0 ]] && cmp -s "$golden" "$typed_out"; then
      n_ok=$((n_ok + 1))
      RESULTS+=("OK             $label  [golden]")
      return
    fi
    n_typed_runtime=$((n_typed_runtime + 1))
    RESULTS+=("TYPED-GOLDEN   $label  (typed exit=$typed_rc)")
    RESULTS+=("  golden: $(head -1 "$golden" 2>/dev/null || true)")
    RESULTS+=("  typed : $(head -1 "$typed_out" 2>/dev/null || true)")
    return
  fi

  # Compare stdout (file-to-file to preserve trailing newlines) and exit code
  if [[ "$typed_rc" -eq "$direct_rc" ]] && cmp -s "$direct_out" "$typed_out"; then
    n_ok=$((n_ok + 1))
    RESULTS+=("OK             $label")
    return
  fi

  # Mismatch — TYPED-RUNTIME
  n_typed_runtime=$((n_typed_runtime + 1))
  local direct_head typed_head
  direct_head="$(head -3 "$direct_out" 2>/dev/null || true)"
  typed_head="$(head -3 "$typed_out" 2>/dev/null || true)"
  RESULTS+=("TYPED-RUNTIME  $label  (direct exit=$direct_rc, typed exit=$typed_rc)")
  RESULTS+=("  direct stdout (first 3 lines): ${direct_head:-(empty)}")
  RESULTS+=("  typed  stdout (first 3 lines): ${typed_head:-(empty)}")
}

# --------------------------------------------------------------------------
# Main: iterate corpus
# --------------------------------------------------------------------------
echo "==> ir-runtime-parity: scanning corpus..."
echo ""

for src in \
    "$REPO_ROOT"/examples/*.sprout \
    "$REPO_ROOT"/tests/smoke_shapes/*.spr \
    "$REPO_ROOT"/tests/stdlib/*.spr; do
  [[ -f "$src" ]] || continue
  process_file "$src"
done

# --------------------------------------------------------------------------
# Print table
# --------------------------------------------------------------------------
echo "--- Per-file results ---"
for line in "${RESULTS[@]}"; do
  echo "$line"
done
echo ""

echo "==> ir-runtime-parity: $n_runnable runnable, $n_ok OK, $n_typed_runtime TYPED-RUNTIME, $n_typed_compile TYPED-COMPILE, $n_typed_link TYPED-LINK, $n_skipped skipped"

# Exit nonzero if any typed failures among runnable files
if [[ $((n_typed_runtime + n_typed_compile + n_typed_link)) -gt 0 ]]; then
  exit 1
fi
exit 0
