#!/usr/bin/env bash
# Whole coverage pipeline. Manual tool, not a gate: `just coverage` (docs: scripts/coverage/README.md).
# Needs build/compile_driver_bin_stage1 (mise exec -- just bootstrap-from-seed).
# Output: $COV_DIR (default build/coverage); reports in $COV_DIR/results/.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$REPO"
[[ -x "$STAGE1" ]] || { echo "ERROR: $STAGE1 missing; run: mise exec -- just bootstrap-from-seed" >&2; exit 1; }
[[ ! -e "$REPO/default.profraw" ]] || { echo "ERROR: $REPO/default.profraw exists before the run; remove it" >&2; exit 1; }

T0=$(date +%s)
step() { echo; echo "==> coverage: $* ($(( $(date +%s) - T0 ))s elapsed)"; }
ORIG_SUM=$(shasum -a 256 "$STAGE1" | cut -d' ' -f1)
FAIL=""

step "build instrumented compiler (sancov + IR PGO)"
bash "$SCRIPTS/build_compiler.sh"
step "full just test under the instrumented compiler"
bash "$SCRIPTS/run_compiler_cov.sh" || FAIL="$FAIL compiler-run(just test failed; see $COV_DIR/compiler_run/just_test.log)"
step "instrumented test binaries"
bash "$SCRIPTS/run_stdlib_cov.sh"
step "tests/c_runtime"
bash "$SCRIPTS/run_crt_cov.sh" || FAIL="$FAIL c_runtime(see $COV_DIR/crt_run/log)"
# The stdlib and compiler analyses read each other's output (executed names; compiler IR rows),
# so run stdlib, compiler, then stdlib again to fill its "+cc%" column. Start from empty results.
rm -rf "$COV_DIR/results"
step "analyze stdlib"
python3 -I "$SCRIPTS/analyze_stdlib.py" > /dev/null
step "analyze compiler"
python3 -I "$SCRIPTS/analyze_compiler.py"
step "analyze stdlib (with compiler union)"
python3 -I "$SCRIPTS/analyze_stdlib.py"
step "analyze runtime"
bash "$SCRIPTS/analyze_runtime.sh"
python3 -I "$SCRIPTS/analyze_runtime_fns.py"

step "checks"
NOW_SUM=$(shasum -a 256 "$STAGE1" | cut -d' ' -f1)
[[ "$ORIG_SUM" == "$NOW_SUM" ]] || { echo "ERROR: stage-1 differs from the start of the run" >&2; exit 1; }
[[ ! -e "$STAGE1_BACKUP" ]] || { echo "ERROR: stage-1 swap backup still present" >&2; exit 1; }
[[ ! -e "$REPO/default.profraw" ]] || { echo "ERROR: default.profraw leaked into the repo root: an instrumented run lacked LLVM_PROFILE_FILE" >&2; exit 1; }
echo "stage-1 restored byte-identical; no default.profraw in the repo root"
echo "==> coverage done in $(( $(date +%s) - T0 ))s; reports in $COV_DIR/results/"
if [[ -n "$FAIL" ]]; then echo "ERROR: failed steps:$FAIL (numbers above are from a partial run)" >&2; exit 1; fi
