set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

stdlib_root := justfile_directory() / "stdlib"
driver      := stdlib_root / "compiler" / "compile_driver.sprout"
clang_extra := if os() == "macos" { "-framework Security -framework CoreFoundation" } else { "" }

default:
  @just --list

# Wire the tracked .githooks/ directory as the active hook path (run once after cloning).
install-hooks:
  git config core.hooksPath .githooks
  @echo "Hooks installed — .githooks/pre-commit is now active."

# Launch the interactive Sprout REPL.
# Prerequisites: just build-repl && just build-analysis-service
repl:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./repl_bin" ]]; then
    echo "ERROR: repl_bin not found; run: just build-repl" >&2; exit 1
  fi
  if [[ ! -x "./analysis_service_bin" ]]; then
    echo "ERROR: analysis_service_bin not found; run: just build-analysis-service" >&2; exit 1
  fi
  SPROUT_SERVICE_CMD="$(pwd)/analysis_service_bin {{stdlib_root}}"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    exec env SPROUT_ANALYSIS_SERVICE_CMD="$SPROUT_SERVICE_CMD" SPROUT_DARWIN_FRAMEWORKS=1 ./repl_bin
  else
    exec env SPROUT_ANALYSIS_SERVICE_CMD="$SPROUT_SERVICE_CMD" ./repl_bin
  fi

# ── Formatting & Linting ──────────────────────────────────────────────────────

[private]
_require-fmt-bin:
  @[[ -x "./fmt_bin" ]] || { echo "ERROR: fmt_bin not found; run: just build-fmt-from-seed" >&2; exit 1; }

[group('fmt')]
fmt: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 ./fmt_bin fmt

[group('fmt')]
fmt-check: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 ./fmt_bin fmt --check

[group('fmt')]
fmt-file file: _require-fmt-bin
  ./fmt_bin fmt {{quote(file)}}

[group('fmt')]
fmt-check-file file: _require-fmt-bin
  ./fmt_bin fmt --check {{quote(file)}}

[group('fmt')]
lint: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 ./fmt_bin lint

[group('fmt')]
lint-file file: _require-fmt-bin
  ./fmt_bin lint {{quote(file)}}

# Build fmt_bin from the committed platform bootstrap seed — no Python required.
[group('fmt')]
build-fmt-from-seed:
  #!/usr/bin/env bash
  set -euo pipefail
  PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
  SEED="bootstrap/compile_driver-$PLATFORM"
  if [[ ! -x "$SEED" ]]; then
    echo "ERROR: No seed binary for platform $PLATFORM at $SEED" >&2
    echo "       Run: just build-seeds  (or build-seed-macos / build-seed-linux)" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_fmt_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Using seed: $SEED ($(file -b "$SEED"))"
  echo "==> Emitting LLVM IR for fmt_bin..."
  "$SEED" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/fmt_driver.sprout" > "$TMP_LL"
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o fmt_bin
  echo "==> Built fmt_bin"

# ── Check / Run ───────────────────────────────────────────────────────────────

[group('dev')]
check file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  ./compile_driver_bin_stage1 --phase check "{{stdlib_root}}" {{quote(file)}}

# Compile {{file}} with stage-1 and run the resulting binary.
[group('dev')]
run file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_run_$$.ll"
  TMP_BIN="/tmp/sprout_run_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  ./compile_driver_bin_stage1 --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMP_BIN"
  "$TMP_BIN"

# Emit LLVM IR for {{file}} to {{out}} using stage-1.
[group('dev')]
compile file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  ./compile_driver_bin_stage1 --emit-ir "{{stdlib_root}}" {{quote(file)}} > {{quote(out)}}

# Compile {{file}} to a native binary at {{out}} using stage-1.
[group('dev')]
compile-native file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_compile_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  ./compile_driver_bin_stage1 --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o {{quote(out)}}

# ── Testing ───────────────────────────────────────────────────────────────────

# Run all stdlib + compiler-stage tests (stage-1).
[group('test')]
test: test-stdlib-stage1

[private]
_test-stdlib stage:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_test_$$.ll"
  TMP_BIN="/tmp/sprout_testbin_$$"
  TMP_ERR="/tmp/sprout_testerr_$$.txt"
  TMP_RT="/tmp/sprout_runtime_$$.o"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR" "$TMP_RT"' EXIT
  # Pre-compile the runtime once; each test just links the resulting .o.
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMP_RT" 2>"$TMP_ERR" || { echo "ERROR: runtime compile failed"; cat "$TMP_ERR"; exit 1; }
  total_failed=0
  for dir in tests/stdlib tests/stdlib/compiler; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.spr "$dir"/*.sprout; do
      [ -f "$f" ] || continue
      echo "==> $f"
      if ! "./$STAGE" --emit-ir "{{stdlib_root}}" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
        echo "  COMPILE FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if ! clang "$TMP_LL" "$TMP_RT" {{clang_extra}} -o "$TMP_BIN" 2>"$TMP_ERR"; then
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

# Stage-1: emit IR → clang link → run for each test file.
[group('test')]
test-stdlib-stage1: (_test-stdlib "compile_driver_bin_stage1")

# Stage-2: emit IR → clang link → run for each test file.
[group('test')]
test-stdlib-stage2: (_test-stdlib "compile_driver_bin_stage2")

[group('test')]
c-runtime-test:
  bash tests/c_runtime/run.sh

# Lint the C runtime for GC safety: const char* params used after gc_maybe_collect.
# Use --strict to exit 1 on findings (CI gate).
[group('test')]
gc-safety-check *args:
  bash scripts/gc_safety_check.sh {{args}}

# ── Compiler Stages ───────────────────────────────────────────────────────────

[private]
_build-stage in_bin out_bin:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./{{in_bin}}" ]]; then
    echo "ERROR: {{in_bin}} not found" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_build_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via {{in_bin}}..."
  ./{{in_bin}} --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{out_bin}}"
  echo "==> Built {{out_bin}}"

# Build compile_driver_bin_stage2 from stage-1.
[group('build')]
build-stage2: (_build-stage "compile_driver_bin_stage1" "compile_driver_bin_stage2")

# Build compile_driver_bin_stage3 from stage-2.
[group('build')]
build-stage3: (_build-stage "compile_driver_bin_stage2" "compile_driver_bin_stage3")

# Build stage-2 with AddressSanitizer + UBSan (slow; for debugging only).
[group('build')]
build-stage2-asan:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_stage2_asan_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via stage-1..."
  ./compile_driver_bin_stage1 --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  echo "==> Linking with clang + ASan/UBSan..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O1 -fsanitize=address,undefined {{clang_extra}} -o compile_driver_bin_stage2_asan
  echo "==> Built compile_driver_bin_stage2_asan (asan)"

# ── Examples ──────────────────────────────────────────────────────────────────

# Compile all examples using stage-1 (default).
[group('examples')]
compile-examples: compile-examples-stage1

# Compile all examples for every stage (1–3).
[group('examples')]
compile-examples-all: compile-examples-stage1 compile-examples-stage2 compile-examples-stage3

[private]
_compile-examples stage xfail="":
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  XFAIL_EXAMPLES="{{xfail}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_ex_$$.ll"
  TMP_BIN="/tmp/sprout_exbin_$$"
  TMP_ERR="/tmp/sprout_exerr_$$.txt"
  trap 'rm -f "$TMP_LL" "$TMP_BIN" "$TMP_ERR"' EXIT
  total_failed=0
  total_xfail=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    echo "==> $f"
    is_xfail=0
    for xf in $XFAIL_EXAMPLES; do [[ "$f" == "$xf" ]] && is_xfail=1 && break; done
    ok=1
    if ! "./$STAGE" --emit-ir "{{stdlib_root}}" "$f" > "$TMP_LL" 2>"$TMP_ERR"; then
      echo "  COMPILE FAILED:"; cat "$TMP_ERR"; ok=0
    elif ! clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMP_BIN" 2>"$TMP_ERR"; then
      echo "  LINK FAILED:"; cat "$TMP_ERR"; ok=0
    fi
    if [[ $ok -eq 1 ]]; then
      if [[ $is_xfail -eq 1 ]]; then echo "  UNEXPECTED OK (remove from xfail)"; total_failed=$((total_failed + 1))
      else echo "  OK"; fi
    else
      if [[ $is_xfail -eq 1 ]]; then echo "  xfail (expected)"; total_xfail=$((total_xfail + 1))
      else total_failed=$((total_failed + 1)); fi
    fi
  done
  echo ""
  [[ $total_xfail -gt 0 ]] && echo "==> $total_xfail example(s) xfail (expected)"
  if [ "$total_failed" -gt 0 ]; then
    echo "==> $total_failed example(s) FAILED"
    exit 1
  fi
  echo "==> All examples compiled OK"

# Stage-1: emit IR → clang link for each example.
# Known xfail: sentry_api (no main fn), sentry_issue_browser{,_tui} (import examples.* unresolved),
[group('examples')]
compile-examples-stage1: (_compile-examples "compile_driver_bin_stage1" "examples/sentry_api.sprout examples/sentry_issue_browser.sprout examples/sentry_issue_browser_tui.sprout")

# Stage-2: emit IR → clang link for each example.
[group('examples')]
compile-examples-stage2: (_compile-examples "compile_driver_bin_stage2")

# Stage-3: emit IR → clang link for each example.
[group('examples')]
compile-examples-stage3: (_compile-examples "compile_driver_bin_stage3")

# ── Bootstrap & Seeds ─────────────────────────────────────────────────────────

# Bootstrap compile_driver_bin_stage1 from the committed platform seed — no Python required.
[group('bootstrap')]
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
  TMP_LL="/tmp/sprout_bootstrap_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Using seed: $SEED ($(file -b "$SEED"))"
  echo "==> Emitting LLVM IR..."
  "$SEED" --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o compile_driver_bin_stage1
  echo "==> Built compile_driver_bin_stage1 from seed (Python-free)"

# Copy stage-1 as the macOS arm64 bootstrap seed. Output: bootstrap/compile_driver-darwin-arm64
[group('bootstrap')]
build-seed-macos:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  mkdir -p bootstrap
  cp compile_driver_bin_stage1 bootstrap/compile_driver-darwin-arm64
  echo "==> bootstrap/compile_driver-darwin-arm64:"
  file bootstrap/compile_driver-darwin-arm64
  ls -lh bootstrap/compile_driver-darwin-arm64

# Build the Linux arm64 bootstrap seed via Docker (ubuntu:24.04, native arm64).
[group('bootstrap')]
build-seed-linux:
  #!/usr/bin/env bash
  set -euo pipefail
  mkdir -p bootstrap
  docker run --rm \
    --platform linux/arm64 \
    --workdir /repo \
    -v "$(pwd):/repo:ro" \
    -v "$(pwd)/bootstrap:/out" \
    ubuntu:24.04 \
    bash -euo pipefail -c '
      apt-get update -qq && apt-get install -y -q clang-16 >&2
      SEED="/repo/bootstrap/compile_driver-linux-aarch64"
      TMP_LL="/tmp/sprout_aarch64_$$.ll"
      echo "==> Using seed: $SEED" >&2
      echo "==> Emitting LLVM IR..." >&2
      "$SEED" --emit-ir /repo/stdlib /repo/stdlib/compiler/compile_driver.sprout > "$TMP_LL"
      echo "==> Linking with clang-16..." >&2
      clang-16 "$TMP_LL" /repo/runtime/sprout_runtime.c -O2 -o /out/compile_driver-linux-aarch64
      chmod +x /out/compile_driver-linux-aarch64
      echo "==> Done" >&2
    '
  echo "==> bootstrap/compile_driver-linux-aarch64:"
  file bootstrap/compile_driver-linux-aarch64
  ls -lh bootstrap/compile_driver-linux-aarch64

# Copy stage-1 as the Linux x86_64 bootstrap seed (run on a Linux x86_64 host).
[group('bootstrap')]
build-seed-linux-amd64:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  mkdir -p bootstrap
  cp compile_driver_bin_stage1 bootstrap/compile_driver-linux-x86_64
  echo "==> bootstrap/compile_driver-linux-x86_64:"
  file bootstrap/compile_driver-linux-x86_64
  ls -lh bootstrap/compile_driver-linux-x86_64

# Build seed binaries for darwin-arm64 + linux-aarch64 (via Docker).
# For linux-x86_64, run just build-seed-linux-amd64 on a Linux x86_64 host.
[group('bootstrap')]
build-seeds: build-seed-macos build-seed-linux

# ── REPL ──────────────────────────────────────────────────────────────────────

# Build the REPL binary from examples/repl_hosted.sprout using stage-1.
[group('build')]
build-repl:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_repl_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for repl_bin..."
  ./compile_driver_bin_stage1 --emit-ir "{{stdlib_root}}" "{{justfile_directory()}}/examples/repl_hosted.sprout" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o repl_bin
  echo "==> Built repl_bin"

# ── Analysis Service ──────────────────────────────────────────────────────────

# Build the analysis service binary. Prefers stage-2; falls back to stage-1.
[group('service')]
build-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ -x "./compile_driver_bin_stage2" ]]; then
    STAGE="compile_driver_bin_stage2"
  elif [[ -x "./compile_driver_bin_stage1" ]]; then
    STAGE="compile_driver_bin_stage1"
  else
    echo "ERROR: neither compile_driver_bin_stage2 nor compile_driver_bin_stage1 found" >&2; exit 1
  fi
  echo "==> Using compiler: $STAGE"
  TMP_LL="/tmp/sprout_analysis_service_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for analysis service..."
  "./$STAGE" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/analysis_service_driver.sprout" > "$TMP_LL"
  echo "==> Validating IR..."
  if command -v opt &>/dev/null; then opt --passes=verify "$TMP_LL" -o /dev/null; else echo "    (opt not found, skipping IR validation)"; fi
  echo "==> Linking with clang..."
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o analysis_service_bin
  echo "==> Built analysis_service_bin"

# Run the analysis service in foreground (reads JSON from stdin, writes to stdout).
# Example: echo '{"op":"declared_names_in_source","module_source":"fn foo() -> Int = 1"}' | just run-analysis-service
[group('service')]
run-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "./analysis_service_bin" ]]; then
    echo "ERROR: analysis_service_bin not found; run: just build-analysis-service" >&2; exit 1
  fi
  exec ./analysis_service_bin "{{stdlib_root}}"
