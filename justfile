set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
  @just --list

# Wire the tracked .githooks/ directory as the active hook path (run once after cloning).
install-hooks:
  git config core.hooksPath .githooks
  @echo "Hooks installed — .githooks/pre-commit is now active."

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

c-runtime-test:
  bash tests/c_runtime/run.sh

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

# Build the native formatter binary (fmt_bin) using the Python stage-0 compiler.
build-fmt:
  #!/usr/bin/env bash
  set -euo pipefail
  DRIVER="stdlib/compiler/fmt_driver.sprout"
  OUT="fmt_bin"
  python3 -m sprout.cli compile --with-stdlib --native -o "$OUT" "$DRIVER"
  echo "==> $OUT built"

fmt-native file:
  ./fmt_bin fmt {{quote(file)}}

fmt-check-native file:
  ./fmt_bin fmt --check {{quote(file)}}

lint-native file:
  ./fmt_bin lint {{quote(file)}}

check file:
  python3 -m sprout.cli check {{file}}

check-stdlib file:
  python3 -m sprout.cli check --with-stdlib {{file}}

# Compile {{file}} with stage-1 and run the resulting binary (always includes stdlib).
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
run file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just build-stage1" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_LL="/tmp/sprout_run_$$.ll"
  TMP_BIN="/tmp/sprout_run_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  ./compile_driver_bin_stage1 --emit-ir "$STDLIB_ROOT" {{quote(file)}} > "$TMP_LL"
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN"
  "$TMP_BIN"

# Alias for `run`: stage-1 always includes stdlib, so run-stdlib is equivalent.
run-stdlib file: (run file)

# Run all in-language stdlib unit tests (.spr/.sprout files under tests/stdlib/).
# Alias for test-stdlib-stage1 (native self-hosted binary).
test-stdlib: test-stdlib-stage1

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
  TMP_LL="/tmp/sprout_test_$$.ll"
  TMP_BIN="/tmp/sprout_testbin_$$"
  TMP_ERR="/tmp/sprout_testerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
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
      if ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
        echo "  LINK FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if out=$("$TMP_BIN" 2>&1); then
        echo "$out"
      else
        status=$?
        echo "$out"
        echo "  RUN FAILED: exit $status"
        total_failed=$((total_failed + 1)); continue
      fi
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
  TMP_LL="/tmp/sprout_test_$$.ll"
  TMP_BIN="/tmp/sprout_testbin_$$"
  TMP_ERR="/tmp/sprout_testerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
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
      if ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
        echo "  LINK FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if out=$("$TMP_BIN" 2>&1); then
        echo "$out"
      else
        status=$?
        echo "$out"
        echo "  RUN FAILED: exit $status"
        total_failed=$((total_failed + 1)); continue
      fi
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

# Emit LLVM IR for {{file}} using the stage-1 self-hosted binary (always includes stdlib).
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
# For the Python stage-0 path: python3 -m sprout.cli compile {{file}} -o {{out}}
compile file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just build-stage1" >&2
    exit 1
  fi
  ./compile_driver_bin_stage1 --emit-ir "$(pwd)/stdlib" {{quote(file)}} > {{quote(out)}}

# Compile {{file}} to a native binary using stage-1 IR emission + clang link.
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
compile-native file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just build-stage1" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_LL="/tmp/sprout_compile_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  ./compile_driver_bin_stage1 --emit-ir "$STDLIB_ROOT" {{quote(file)}} > "$TMP_LL"
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o {{quote(out)}}

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
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via Sprout-native codegen..."
  ./compile_driver_bin --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$STAGE1"
  echo "==> Built $STAGE1"

build-stage2:
  #!/usr/bin/env bash
  set -euo pipefail
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE1="compile_driver_bin_stage1"
  STAGE2="compile_driver_bin_stage2"
  TMP_LL="/tmp/sprout_stage2_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  if [[ ! -x "$STAGE1" ]]; then
    echo "ERROR: $STAGE1 not found; run: just build-stage1" >&2
    exit 1
  fi
  echo "==> Emitting LLVM IR via stage-1 Sprout-native codegen..."
  "./$STAGE1" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$STAGE2"
  echo "==> Built $STAGE2"

build-stage3:
  #!/usr/bin/env bash
  set -euo pipefail
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  STAGE2="compile_driver_bin_stage2"
  STAGE3="compile_driver_bin_stage3"
  TMP_LL="/tmp/sprout_stage3_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  if [[ ! -x "$STAGE2" ]]; then
    echo "ERROR: $STAGE2 not found; run: just build-stage2" >&2
    exit 1
  fi
  echo "==> Emitting LLVM IR via stage-2 Sprout-native codegen..."
  "./$STAGE2" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$STAGE3"
  echo "==> Built $STAGE3"

# Copy the current stage-1 binary as the macOS arm64 bootstrap seed.
# Requires compile_driver_bin_stage1; build it first with: just build-stage1
# Output: bootstrap/compile_driver-darwin-arm64
build-seed-macos:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just build-stage1" >&2
    exit 1
  fi
  mkdir -p bootstrap
  cp compile_driver_bin_stage1 bootstrap/compile_driver-darwin-arm64
  echo "==> bootstrap/compile_driver-darwin-arm64:"
  file bootstrap/compile_driver-darwin-arm64
  ls -lh bootstrap/compile_driver-darwin-arm64

# Build the Linux arm64 bootstrap seed binary via Docker (ubuntu:24.04, native arm64).
# Starts a throwaway container, builds stage-0 from Python then stage-1 inside it, copies the
# resulting ELF binary back to bootstrap/compile_driver-linux-aarch64.
# Requires: Docker on PATH and running.
build-seed-linux:
  #!/usr/bin/env bash
  set -euo pipefail
  mkdir -p bootstrap
  docker run --rm \
    --workdir /repo \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$(pwd):/repo:ro" \
    -v "$(pwd)/bootstrap:/out" \
    ubuntu:24.04 \
    bash -euo pipefail -c '
      apt-get update -qq && apt-get install -y -q clang llvm python3 >&2
      echo "==> stage-0: Python compiler → native binary" >&2
      python3 -m sprout.cli compile \
        stdlib/compiler/compile_driver.sprout \
        --with-stdlib --native -o /tmp/stage0
      echo "==> stage-1: stage-0 IR → clang link" >&2
      /tmp/stage0 --emit-ir /repo/stdlib stdlib/compiler/compile_driver.sprout > /tmp/stage1.ll
      clang /tmp/stage1.ll runtime/sprout_runtime.c -O2 -o /out/compile_driver-linux-aarch64
      echo "==> Done" >&2
    '
  chmod +x bootstrap/compile_driver-linux-aarch64
  echo "==> bootstrap/compile_driver-linux-aarch64:"
  file bootstrap/compile_driver-linux-aarch64
  ls -lh bootstrap/compile_driver-linux-aarch64

# Build the Linux x86_64 bootstrap seed binary — run this natively on a Linux x86_64 host.
# Requires compile_driver_bin (stage-0) and clang on PATH.
# Output: bootstrap/compile_driver-linux-x86_64
build-seed-linux-amd64:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin" ]]; then
    echo "ERROR: compile_driver_bin not found; run: just build-stage0" >&2
    exit 1
  fi
  mkdir -p bootstrap
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  OUT="bootstrap/compile_driver-linux-x86_64"
  TMP_LL="/tmp/sprout_seed_amd64_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via stage-0..."
  ./compile_driver_bin --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 -o "$OUT"
  chmod +x "$OUT"
  echo "==> $OUT:"
  file "$OUT"
  ls -lh "$OUT"

# Build bootstrap seed binaries for Mac-buildable platforms (darwin-arm64 + linux-aarch64 via Docker).
# For linux-x86_64, run: just build-seed-linux-amd64  (on a Linux x86_64 host)
build-seeds: build-seed-macos build-seed-linux

# Bootstrap compile_driver_bin_stage1 from the committed platform seed — no Python required.
# Detects the current platform via uname and selects bootstrap/compile_driver-<os>-<arch>.
# Output: compile_driver_bin_stage1
bootstrap-from-seed:
  #!/usr/bin/env bash
  set -euo pipefail
  PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
  SEED="bootstrap/compile_driver-$PLATFORM"
  if [[ ! -x "$SEED" ]]; then
    echo "ERROR: No seed binary for platform $PLATFORM at $SEED" >&2
    echo "       Run: just build-seeds  (or build-seed-linux / build-seed-macos)" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/compile_driver.sprout"
  TMP_LL="/tmp/sprout_bootstrap_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Using seed: $SEED ($(file -b "$SEED"))"
  echo "==> Emitting LLVM IR..."
  "$SEED" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  # Compat shim: seeds built before the llvm.stacksave/stackrestore declare fix don't emit
  # these declarations. clang 16+ requires explicit declares; inject them if missing.
  if ! grep -qF 'declare ptr @llvm.stacksave' "$TMP_LL"; then
    TMP_PATCH="/tmp/sprout_bootstrap_patch_$$"
    { head -3 "$TMP_LL"
      printf 'declare ptr @llvm.stacksave()\ndeclare void @llvm.stackrestore(ptr)\n'
      tail -n +4 "$TMP_LL"
    } > "$TMP_PATCH" && mv "$TMP_PATCH" "$TMP_LL"
  fi
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o compile_driver_bin_stage1
  echo "==> Built compile_driver_bin_stage1 from seed (Python-free)"

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
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via Sprout-native codegen..."
  ./compile_driver_bin --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Linking with clang + ASan/UBSan..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O1 -fsanitize=address,undefined $CLANG_EXTRA -o "$STAGE1"
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
  trap 'rm -f "$TMP_LL"' EXIT
  if [[ ! -x "$STAGE1" ]]; then
    echo "ERROR: $STAGE1 not found; run: just build-stage1" >&2
    exit 1
  fi
  echo "==> Emitting LLVM IR via stage-1 Sprout-native codegen..."
  "./$STAGE1" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Linking with clang + ASan/UBSan..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O1 -fsanitize=address,undefined $CLANG_EXTRA -o "$STAGE2"
  echo "==> Built $STAGE2 (asan)"

# Build the self-hosted analysis service binary (Phase 9).
# Requires compile_driver_bin_stage2 (preferred) or compile_driver_bin_stage1 as fallback.
# Output: analysis_service_bin — JSON-over-stdio daemon used by the language server bridge.
build-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  # Prefer stage2 (includes recent type-checker fixes); fall back to stage1.
  if [[ -x "./compile_driver_bin_stage2" ]]; then
    STAGE="compile_driver_bin_stage2"
  elif [[ -x "./compile_driver_bin_stage1" ]]; then
    STAGE="compile_driver_bin_stage1"
  else
    echo "ERROR: neither compile_driver_bin_stage2 nor compile_driver_bin_stage1 found" >&2
    exit 1
  fi
  echo "==> Using compiler: $STAGE"
  STDLIB_ROOT="$(pwd)/stdlib"
  DRIVER="stdlib/compiler/analysis_service_driver.sprout"
  OUT="analysis_service_bin"
  TMP_LL="/tmp/sprout_analysis_service_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for analysis service..."
  "./$STAGE" --emit-ir "$STDLIB_ROOT" "$DRIVER" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$OUT"
  echo "==> Built $OUT"

# Run the self-hosted analysis service binary in foreground (for manual testing / smoke checks).
# Requires analysis_service_bin; build it first with: just build-analysis-service
# The binary reads JSON requests from stdin and writes JSON responses to stdout.
# Example: echo '{"op":"declared_names_in_source","module_source":"fn foo() -> Int = 1"}' | just run-analysis-service
run-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  BIN="./analysis_service_bin"
  if [[ ! -x "$BIN" ]]; then
    echo "ERROR: analysis_service_bin not found; run: just build-analysis-service" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  exec "$BIN" "$STDLIB_ROOT"

# Start an interactive Sprout REPL (native self-hosted binary; requires clang and compile_driver_bin_stage1).
repl:
  python3 -m sprout.cli repl

# Start the native Sprout REPL (compiled binary, analysis-service-backed).
# Uses analysis_service_bin for analysis if present; falls back to Python analysis service.
# Requires clang on PATH. Build analysis_service_bin first with: just build-analysis-service
repl-native:
  python3 -m sprout.cli repl --native

# Compile all examples to LLVM IR. Alias for compile-examples-stage1 (stage-1 self-hosted binary).
# For the Python stage-0 path: just compile-examples-stage0
compile-examples: compile-examples-stage1

# Run compile-examples for every available compiler stage (0-3).
compile-examples-all: compile-examples-stage0 compile-examples-stage1 compile-examples-stage2 compile-examples-stage3

# Compile all examples using the committed platform bootstrap seed binary.
# Detects the current platform (e.g. darwin-arm64, linux-x86_64) and selects bootstrap/compile_driver-<os>-<arch>.
# Useful for verifying that the committed seed still correctly compiles all examples.
compile-examples-bootstrap:
  #!/usr/bin/env bash
  set -euo pipefail
  PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
  STAGE="bootstrap/compile_driver-$PLATFORM"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: No seed binary for platform $PLATFORM at $STAGE" >&2
    echo "       Run: just build-seeds  (or build-seed-macos / build-seed-linux / build-seed-linux-amd64)" >&2
    exit 1
  fi
  echo "==> Using seed: $STAGE ($(file -b "$STAGE"))"
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
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
    if ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
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
  echo "==> All examples compiled OK (bootstrap/$PLATFORM)"

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
  # Known pre-existing failures — see BACKLOG.md for root causes.
  # Remove entries here once the underlying issue is fixed.
  # sentry_api: library module with no main fn; link fails at entry point.
  # sentry_issue_browser{,_tui}: import examples.* which module loader doesn't resolve.
  # ref_tutorial, text_demo: stage-1 parser chokes on these files (non-ASCII / syntax); parser bug.
  XFAIL_EXAMPLES="examples/sentry_api.sprout examples/sentry_issue_browser.sprout examples/sentry_issue_browser_tui.sprout"
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  CLANG_EXTRA=""
  if [[ "$(uname)" == "Darwin" ]]; then CLANG_EXTRA="-framework Security -framework CoreFoundation"; fi
  total_failed=0
  total_xfail=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    echo "==> $f"
    is_xfail=0
    for xf in $XFAIL_EXAMPLES; do [[ "$f" == "$xf" ]] && is_xfail=1 && break; done
    ok=1
    if ! "./$STAGE" --emit-ir "$STDLIB_ROOT" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
      echo "  COMPILE FAILED:"; cat "$TMP_ERR"; ok=0
    elif ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
      echo "  LINK FAILED:"; cat "$TMP_ERR"; ok=0
    fi
    if [[ $ok -eq 1 ]]; then
      if [[ $is_xfail -eq 1 ]]; then echo "  UNEXPECTED OK (remove from XFAIL_EXAMPLES)"; total_failed=$((total_failed + 1))
      else echo "  OK"; fi
    else
      if [[ $is_xfail -eq 1 ]]; then echo "  xfail (expected)"; total_xfail=$((total_xfail + 1))
      else total_failed=$((total_failed + 1)); fi
    fi
  done
  echo ""
  [[ $total_xfail -gt 0 ]] && echo "==> $total_xfail example(s) xfail (expected, see XFAIL_EXAMPLES)"
  if [ "$total_failed" -gt 0 ]; then
    echo "==> $total_failed example(s) FAILED"
    exit 1
  fi
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
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
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
    if ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
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
  echo "==> All examples compiled OK (stage-2)"

# Stage-3 (stage-3 self-hosted binary): emit IR → clang link for each example.
# Requires compile_driver_bin_stage3; build it first with: just build-stage3
compile-examples-stage3:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="compile_driver_bin_stage3"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found; run: just build-stage3" >&2
    exit 1
  fi
  STDLIB_ROOT="$(pwd)/stdlib"
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
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
    if ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 $CLANG_EXTRA -o "$TMP_BIN" 2>"$TMP_ERR"; then
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
  echo "==> All examples compiled OK (stage-3)"
