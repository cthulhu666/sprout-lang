set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
  @just --list

test *mods:
  python3 scripts/run_parallel_tests.py {{mods}}

test-serial:
  python3 -m unittest discover -s tests -v

test-all:
  python3 -m unittest discover -s tests -v

test-parallel *mods:
  python3 scripts/run_parallel_tests.py {{mods}}

test-integration:
  python3 -m unittest discover -s tests -p 'test_integration_io.py' -v

measure-gc-thresholds:
  python3 scripts/measure_gc_thresholds.py

measure-gc-real:
  python3 scripts/measure_gc_thresholds.py --include-real

# Lint the C runtime for GC safety: const char* params used after gc_maybe_collect.
# Use --strict to exit 1 on findings (CI gate).
gc-safety-check *args:
  python3 scripts/gc_safety_check.py {{args}}

parse file:
  python3 -m sprout.cli parse {{file}}

fmt:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli fmt

fmt-check:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli fmt --check

fmt-file file:
  python3 -m sprout.cli fmt {{file}}

fmt-check-file file:
  python3 -m sprout.cli fmt --check {{file}}

lint:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli lint

lint-file file:
  python3 -m sprout.cli lint {{file}}

check file:
  python3 -m sprout.cli check {{file}}

check-stdlib file:
  python3 -m sprout.cli check --with-stdlib {{file}}

run file:
  python3 -m sprout.cli run {{file}}

run-stdlib file:
  python3 -m sprout.cli run --with-stdlib {{file}}

# Run all in-language stdlib unit tests (.spr/.sprout files under tests/stdlib/).
# Alias for test-stdlib-stage0 (stage-0 Python compiler).
test-stdlib: test-stdlib-stage0

# Stage-0 (Python CLI): run stdlib unit tests via python3 -m sprout.cli run --with-stdlib.
test-stdlib-stage0:
  #!/usr/bin/env bash
  set -euo pipefail
  total_failed=0
  for dir in tests/stdlib tests/stdlib/compiler; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.spr "$dir"/*.sprout; do
      [ -f "$f" ] || continue
      echo "==> $f"
      out=$(python3 -m sprout.cli run --with-stdlib "$f" 2>&1)
      echo "$out"
      if echo "$out" | grep -q "^SUITE FAILED"; then
        total_failed=$((total_failed + 1))
      fi
    done
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed test suite(s) FAILED"
    exit 1
  fi
  echo ""
  echo "==> All suites PASSED"

# Stage-1 (native self-hosted binary): emit IR → clang link → run for each test file.
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
test-stdlib-stage1:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="compile_driver_bin_stage1"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found; run: just build-stage1" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_RT="/tmp/sprout_rt_$$.c"
  TMP_LL="/tmp/sprout_test_$$.ll"
  TMP_BIN="/tmp/sprout_testbin_$$"
  TMP_ERR="/tmp/sprout_testerr_$$.txt"
  trap 'rm -f "$TMP_RT" "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_RT" --with-stdlib -o /dev/null stdlib/compiler/compile_driver.sprout
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  total_failed=0
  for dir in tests/stdlib tests/stdlib/compiler; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.spr "$dir"/*.sprout; do
      [ -f "$f" ] || continue
      echo "==> $f"
      if ! "./$STAGE" --emit-ir "$STDLIB_ROOT" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
        echo "  COMPILE FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if ! clang "$TMP_LL" "$TMP_RT" -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
        echo "  LINK FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      out=$("$TMP_BIN" 2>&1) || true
      echo "$out"
      if echo "$out" | grep -q "^SUITE FAILED"; then
        total_failed=$((total_failed + 1))
      fi
    done
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed test suite(s) FAILED"
    exit 1
  fi
  echo ""
  echo "==> All suites PASSED"

# Stage-2 (stage-2 self-hosted binary): emit IR → clang link → run for each test file.
# Requires compile_driver_bin_stage2; build it first with: just build-stage2
test-stdlib-stage2:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="compile_driver_bin_stage2"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found; run: just build-stage2" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_RT="/tmp/sprout_rt_$$.c"
  TMP_LL="/tmp/sprout_test_$$.ll"
  TMP_BIN="/tmp/sprout_testbin_$$"
  TMP_ERR="/tmp/sprout_testerr_$$.txt"
  trap 'rm -f "$TMP_RT" "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_RT" --with-stdlib -o /dev/null stdlib/compiler/compile_driver.sprout
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  total_failed=0
  for dir in tests/stdlib tests/stdlib/compiler; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.spr "$dir"/*.sprout; do
      [ -f "$f" ] || continue
      echo "==> $f"
      if ! "./$STAGE" --emit-ir "$STDLIB_ROOT" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
        echo "  COMPILE FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if ! clang "$TMP_LL" "$TMP_RT" -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
        echo "  LINK FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      out=$("$TMP_BIN" 2>&1) || true
      echo "$out"
      if echo "$out" | grep -q "^SUITE FAILED"; then
        total_failed=$((total_failed + 1))
      fi
    done
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed test suite(s) FAILED"
    exit 1
  fi
  echo ""
  echo "==> All suites PASSED"

compile file out:
  python3 -m sprout.cli compile {{file}} -o {{out}}

compile-native file out:
  python3 -m sprout.cli compile {{file}} --with-stdlib --native -o {{out}}

# Build compile_driver_bin (stage-0) from Python.
# Output: compile_driver_bin — native binary produced by the Python compiler.
build-stage0:
  python3 -m sprout.cli compile stdlib/compiler/compile_driver.sprout --with-stdlib --native -o compile_driver_bin

# Build compile_driver_bin_stage1 using Sprout-native IR emission (M6 bootstrap).
# Requires compile_driver_bin (stage-0) to already exist.
# Output: compile_driver_bin_stage1 — a native binary produced without Python codegen.
build-stage1:
  #!/usr/bin/env bash
  set -euo pipefail
  if find stdlib/compiler -name "*.sprout" -newer compile_driver_bin 2>/dev/null | grep -q .; then
    echo "WARNING: compiler sources are newer than compile_driver_bin (stage-0); edits won't be in this build." >&2
    echo "WARNING: To include recent edits, rebuild stage-0 from Python first." >&2
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE1="compile_driver_bin_stage1"
  TMP_LL="/tmp/sprout_stage1_$$.ll"
  TMP_C="/tmp/sprout_runtime_$$.c"
  trap 'rm -f "$TMP_LL" "$TMP_C"' EXIT
  echo "==> Emitting LLVM IR via Sprout-native codegen..."
  ./compile_driver_bin --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_C" --with-stdlib -o /dev/null "$DRIVER"
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" "$TMP_C" -O2 $CLANG_EXTRA -o "$STAGE1"
  echo "==> Built $STAGE1"

build-stage2:
  #!/usr/bin/env bash
  set -euo pipefail
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE1="compile_driver_bin_stage1"
  STAGE2="compile_driver_bin_stage2"
  TMP_LL="/tmp/sprout_stage2_$$.ll"
  TMP_C="/tmp/sprout_runtime_$$.c"
  trap 'rm -f "$TMP_LL" "$TMP_C"' EXIT
  if [[ ! -x "$STAGE1" ]]; then
    echo "ERROR: $STAGE1 not found; run: just build-stage1" >&2
    exit 1
  fi
  echo "==> Emitting LLVM IR via stage-1 Sprout-native codegen..."
  "./$STAGE1" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_C" --with-stdlib -o /dev/null "$DRIVER"
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" "$TMP_C" -O2 $CLANG_EXTRA -o "$STAGE2"
  echo "==> Built $STAGE2"

build-stage1-asan:
  #!/usr/bin/env bash
  set -euo pipefail
  if find stdlib/compiler -name "*.sprout" -newer compile_driver_bin 2>/dev/null | grep -q .; then
    echo "WARNING: compiler sources are newer than compile_driver_bin (stage-0); edits won't be in this build." >&2
    echo "WARNING: To include recent edits, rebuild stage-0 from Python first." >&2
  fi
  # Same as build-stage1 but links with AddressSanitizer + UBSan for crash attribution.
  # Use only for debugging; output binary is ~5x slower.
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE1="compile_driver_bin_stage1_asan"
  TMP_LL="/tmp/sprout_stage1_asan_$$.ll"
  TMP_C="/tmp/sprout_runtime_asan_$$.c"
  trap 'rm -f "$TMP_LL" "$TMP_C"' EXIT
  echo "==> Emitting LLVM IR via Sprout-native codegen..."
  ./compile_driver_bin --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_C" --with-stdlib -o /dev/null "$DRIVER"
  echo "==> Linking with clang + ASan/UBSan..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" "$TMP_C" -O1 -fsanitize=address,undefined $CLANG_EXTRA -o "$STAGE1"
  echo "==> Built $STAGE1 (asan)"

build-stage2-asan:
  #!/usr/bin/env bash
  set -euo pipefail
  # Same as build-stage2 but links with AddressSanitizer + UBSan.
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE1="compile_driver_bin_stage1"
  STAGE2="compile_driver_bin_stage2_asan"
  TMP_LL="/tmp/sprout_stage2_asan_$$.ll"
  TMP_C="/tmp/sprout_runtime_asan_$$.c"
  trap 'rm -f "$TMP_LL" "$TMP_C"' EXIT
  if [[ ! -x "$STAGE1" ]]; then
    echo "ERROR: $STAGE1 not found; run: just build-stage1" >&2
    exit 1
  fi
  echo "==> Emitting LLVM IR via stage-1 Sprout-native codegen..."
  "./$STAGE1" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_C" --with-stdlib -o /dev/null "$DRIVER"
  echo "==> Linking with clang + ASan/UBSan..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" "$TMP_C" -O1 -fsanitize=address,undefined $CLANG_EXTRA -o "$STAGE2"
  echo "==> Built $STAGE2 (asan)"

# Compile all examples to LLVM IR. Alias for compile-examples-stage0.
compile-examples: compile-examples-stage0

# Stage-0 (Python CLI): compile each example to LLVM IR via python3 -m sprout.cli compile.
compile-examples-stage0:
  #!/usr/bin/env bash
  set -euo pipefail
  total_failed=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    flags=""
    if [ "$f" = "examples/result_demo.sprout" ]; then flags="--with-stdlib"; fi
    out="/tmp/$(basename "$f" .sprout).ll"
    if python3 -m sprout.cli compile $flags "$f" -o "$out"; then
      echo "OK $f"
    else
      echo "FAILED $f"
      total_failed=$((total_failed + 1))
    fi
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed example(s) FAILED to compile"
    exit 1
  fi
  echo ""
  echo "==> All examples compiled OK"

# Stage-1 (native self-hosted binary): emit IR → clang link for each example.
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
compile-examples-stage1:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="compile_driver_bin_stage1"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found; run: just build-stage1" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_RT="/tmp/sprout_rt_$$.c"
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_RT" "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_RT" --with-stdlib -o /dev/null stdlib/compiler/compile_driver.sprout
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  total_failed=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    echo "==> $f"
    if ! "./$STAGE" --emit-ir "$STDLIB_ROOT" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
      echo "  COMPILE FAILED:"; cat "$TMP_ERR"
      total_failed=$((total_failed + 1)); continue
    fi
    if ! clang "$TMP_LL" "$TMP_RT" -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
      echo "  LINK FAILED:"; cat "$TMP_ERR"
      total_failed=$((total_failed + 1)); continue
    fi
    echo "  OK"
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed example(s) FAILED"
    exit 1
  fi
  echo ""
  echo "==> All examples compiled OK"

# Stage-2 (stage-2 self-hosted binary): emit IR → clang link for each example.
# Requires compile_driver_bin_stage2; build it first with: just build-stage2
compile-examples-stage2:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="compile_driver_bin_stage2"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found; run: just build-stage2" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_RT="/tmp/sprout_rt_$$.c"
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_RT" "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  echo "==> Extracting C runtime..."
  python3 -m sprout.cli compile --emit-runtime-c "$TMP_RT" --with-stdlib -o /dev/null stdlib/compiler/compile_driver.sprout
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  total_failed=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    echo "==> $f"
    if ! "./$STAGE" --emit-ir "$STDLIB_ROOT" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
      echo "  COMPILE FAILED:"; cat "$TMP_ERR"
      total_failed=$((total_failed + 1)); continue
    fi
    if ! clang "$TMP_LL" "$TMP_RT" -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
      echo "  LINK FAILED:"; cat "$TMP_ERR"
      total_failed=$((total_failed + 1)); continue
    fi
    echo "  OK"
  done
  if [ "$total_failed" -gt 0 ]; then
    echo ""
    echo "==> $total_failed example(s) FAILED"
    exit 1
  fi
  echo ""
  echo "==> All examples compiled OK"
