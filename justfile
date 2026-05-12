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

compile file out:
  python3 -m sprout.cli compile {{file}} -o {{out}}

compile-native file out:
  python3 -m sprout.cli compile {{file}} --native -o {{out}}

# Build compile_driver_bin_stage1 using Sprout-native IR emission (M6 bootstrap).
# Requires compile_driver_bin (stage-0) to already exist.
# Output: compile_driver_bin_stage1 — a native binary produced without Python codegen.
build-stage1:
  #!/usr/bin/env bash
  set -euo pipefail
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

compile-examples:
  for file in examples/*.sprout; do \
    flags=""; \
    if [ "$file" = "examples/result_demo.sprout" ]; then flags="--with-stdlib"; fi; \
    out="/tmp/$(basename "$file" .sprout).ll"; \
    python3 -m sprout.cli compile $flags "$file" -o "$out"; \
    echo "OK $file"; \
  done
