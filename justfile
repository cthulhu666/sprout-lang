set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

stdlib_root := justfile_directory() / "stdlib"
driver      := stdlib_root / "compiler" / "compile_driver.sprout"
clang_extra := if os() == "macos" { "-framework Security -framework CoreFoundation" } else { "" }
build_dir   := justfile_directory() / "build"

default:
  @just --list

# Wire the tracked .githooks/ directory as the active hook path (run once after cloning).
install-hooks:
  git config core.hooksPath .githooks
  @echo "Hooks installed — .githooks/pre-commit is now active."

# Acknowledge that DoD criteria are met for the currently staged tree.
# Required by .claude/settings.json PreToolUse Bash hook on `git commit`.
# Writes `git write-tree` hash to .git/dod-ack; hook compares the hash on the
# pending commit's staged tree.  Same tree → ack reused (e.g. amend message).
# Different tree → re-run `just dod-ack` to re-attest.
dod-ack:
  #!/usr/bin/env bash
  set -euo pipefail
  cd "{{invocation_directory()}}"
  ACK="$(git rev-parse --git-dir)/dod-ack"
  git write-tree > "$ACK"
  echo "DoD acked for staged tree: $(cat "$ACK")"

# Bypass the bootstrap seed gate for one commit when a compiler change does not
# affect IR output.  Run `just verify-bootstrap-fixed-point` first to confirm.
seed-fp-ack:
  #!/usr/bin/env bash
  set -euo pipefail
  cd "{{invocation_directory()}}"
  ACK="$(git rev-parse --git-dir)/seed-fp-ack"
  git write-tree > "$ACK"
  echo "Seed fixed-point acked for staged tree: $(cat "$ACK")"

# Launch the interactive Sprout REPL via sproutd (self-configuring).
# Prerequisites: just build-sproutd
repl:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/sproutd" ]]; then
    echo "ERROR: sproutd not found; run: just build-sproutd" >&2; exit 1
  fi
  exec "{{build_dir}}/sproutd"

# ── Formatting & Linting ──────────────────────────────────────────────────────

[private]
_require-fmt-bin:
  @[[ -x "{{build_dir}}/fmt_bin" ]] || { echo "ERROR: fmt_bin not found; run: just build-fmt-from-seed" >&2; exit 1; }

[group('fmt')]
fmt: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" fmt

[group('fmt')]
fmt-check: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" fmt --check

[group('fmt')]
fmt-file file: _require-fmt-bin
  "{{build_dir}}/fmt_bin" fmt {{quote(file)}}

[group('fmt')]
fmt-check-file file: _require-fmt-bin
  "{{build_dir}}/fmt_bin" fmt --check {{quote(file)}}

[group('fmt')]
lint: _require-fmt-bin
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" lint

[group('fmt')]
lint-file file: _require-fmt-bin
  "{{build_dir}}/fmt_bin" lint {{quote(file)}}

# Build fmt_bin via stage-1 (which is built from the IR seed).  fmt_bin chains
# off compile_driver_bin_stage1 — no platform-specific binary required.
[group('fmt')]
build-fmt-from-seed: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_fmt_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for fmt_bin via stage-1..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/fmt_driver.sprout" > "$TMP_LL"
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{build_dir}}/fmt_bin"
  echo "==> Built {{build_dir}}/fmt_bin"

# ── Check / Run ───────────────────────────────────────────────────────────────

[group('dev')]
check file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  "{{build_dir}}/compile_driver_bin_stage1" --phase check "{{stdlib_root}}" {{quote(file)}}

# Compile {{file}} with stage-1 and run the resulting binary.
[group('dev')]
run file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_run_$$.ll"
  TMP_BIN="/tmp/sprout_run_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMP_BIN"
  "$TMP_BIN"

# Emit LLVM IR for {{file}} to {{out}} using stage-1.
[group('dev')]
compile file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > {{quote(out)}}

# Compile {{file}} to a native binary at {{out}} using stage-1.
[group('dev')]
compile-native file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_compile_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o {{quote(out)}}

# Compile {{file}} to a debug binary at {{out}} using stage-1 (DWARF, no optimisation).
# Use: just build-debug path/to/prog.spr ./prog_dbg && lldb ./prog_dbg
[group('dev')]
build-debug file out:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_debug_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir --debug "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -g -O0 {{clang_extra}} -o {{quote(out)}}

# Compile {{file}} with debug info and launch it under lldb.
[group('dev')]
debug-run file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_debug_$$.ll"
  TMP_BIN="/tmp/sprout_debug_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir --debug "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -g -O0 {{clang_extra}} -o "$TMP_BIN"
  lldb "$TMP_BIN"

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
      if ! opt --passes=verify "$TMP_LL" -o /dev/null 2>"$TMP_ERR"; then
        echo "  IR INVALID (opt --passes=verify):"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if ! clang "$TMP_LL" "$TMP_RT" {{clang_extra}} -o "$TMP_BIN" 2>"$TMP_ERR"; then
        echo "  LINK FAILED:"; cat "$TMP_ERR"
        total_failed=$((total_failed + 1)); continue
      fi
      if out=$(SPROUT_STDLIB_ROOT="{{stdlib_root}}" "$TMP_BIN" 2>&1); then
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
test-stdlib-stage1: (_test-stdlib "build/compile_driver_bin_stage1")

# Stage-2: emit IR → clang link → run for each test file.
[group('test')]
test-stdlib-stage2: (_test-stdlib "build/compile_driver_bin_stage2")

# Run a single test file with stage-1.
[group('test')]
test-file file: (_test-file "build/compile_driver_bin_stage1" file)

[private]
_test-file stage file:
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
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMP_RT" 2>"$TMP_ERR" || { echo "ERROR: runtime compile failed"; cat "$TMP_ERR"; exit 1; }
  echo "==> {{file}}"
  if ! "./$STAGE" --emit-ir "{{stdlib_root}}" "{{file}}" > "$TMP_LL" 2>"$TMP_ERR"; then
    echo "  COMPILE FAILED:"; cat "$TMP_ERR"; exit 1
  fi
  if ! clang "$TMP_LL" "$TMP_RT" {{clang_extra}} -o "$TMP_BIN" 2>"$TMP_ERR"; then
    echo "  LINK FAILED:"; cat "$TMP_ERR"; exit 1
  fi
  if out=$("$TMP_BIN" 2>&1); then
    echo "$out"
  else
    status=$?
    echo "$out"
    echo "  RUN FAILED: exit $status"; exit 1
  fi
  if echo "$out" | grep -q "^SUITE FAILED"; then
    exit 1
  fi

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
  opt --passes=verify "$TMP_LL" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{out_bin}}"
  echo "==> Built {{out_bin}}"

# Build compile_driver_bin_stage2 from stage-1.
[group('build')]
build-stage2: (_build-stage "build/compile_driver_bin_stage1" "build/compile_driver_bin_stage2")

# Build compile_driver_bin_stage3 from stage-2.
[group('build')]
build-stage3: (_build-stage "build/compile_driver_bin_stage2" "build/compile_driver_bin_stage3")

# Build stage-2 with AddressSanitizer + UBSan (slow; for debugging only).
[group('build')]
build-stage2-asan:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_stage2_asan_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via stage-1..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  echo "==> Linking with clang + ASan/UBSan..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O1 -fsanitize=address,undefined {{clang_extra}} -o "{{build_dir}}/compile_driver_bin_stage2_asan"
  echo "==> Built {{build_dir}}/compile_driver_bin_stage2_asan (asan)"

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
  NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
  JOBS=$(( NCPU > 8 ? 8 : NCPU ))
  TMPD="/tmp/sprout_ex_$$"
  mkdir -p "$TMPD"
  trap 'rm -rf "$TMPD"' EXIT
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/rt.o" 2>"$TMPD/rt.err" \
    || { echo "ERROR: runtime compile failed"; cat "$TMPD/rt.err"; exit 1; }
  declare -a pids=()
  declare -a outs=()
  declare -a stats=()
  idx=0
  active=0
  for f in examples/*.sprout; do
    [ -f "$f" ] || continue
    outs+=("$TMPD/$idx.out")
    stats+=("$TMPD/$idx.st")
    (
      set +e
      is_xfail=0
      for xf in $XFAIL_EXAMPLES; do [[ "$f" == "$xf" ]] && is_xfail=1 && break; done
      printf '==> %s\n' "$f" > "$TMPD/$idx.out"
      ok=1
      "./$STAGE" --emit-ir "{{stdlib_root}}" "$f" > "$TMPD/$idx.ll" 2>"$TMPD/$idx.err"
      if [[ $? -ne 0 ]]; then
        { printf '  COMPILE FAILED:\n'; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      elif ! opt --passes=verify "$TMPD/$idx.ll" -o /dev/null 2>"$TMPD/$idx.err"; then
        { printf '  IR INVALID (opt --passes=verify):\n'; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      else
        clang "$TMPD/$idx.ll" "$TMPD/rt.o" {{clang_extra}} -o "$TMPD/$idx.bin" 2>"$TMPD/$idx.err"
        if [[ $? -ne 0 ]]; then
          { printf '  LINK FAILED:\n'; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
        fi
      fi
      if [[ $ok -eq 1 ]]; then
        if [[ $is_xfail -eq 1 ]]; then printf '  UNEXPECTED OK (remove from xfail)\n' >> "$TMPD/$idx.out"; echo 2 > "$TMPD/$idx.st"
        else printf '  OK\n' >> "$TMPD/$idx.out"; echo 0 > "$TMPD/$idx.st"; fi
      else
        if [[ $is_xfail -eq 1 ]]; then printf '  xfail (expected)\n' >> "$TMPD/$idx.out"; echo 3 > "$TMPD/$idx.st"
        else echo 1 > "$TMPD/$idx.st"; fi
      fi
    ) &
    pids+=($!)
    idx=$((idx + 1))
    active=$((active + 1))
    if (( active >= JOBS )); then
      wait -n 2>/dev/null || wait "${pids[idx - active]}" || true
      active=$((active - 1))
    fi
  done
  for pid in "${pids[@]}"; do wait "$pid" || true; done
  total_failed=0
  total_xfail=0
  for i in "${!outs[@]}"; do
    cat "${outs[$i]}" 2>/dev/null || true
    case "$(cat "${stats[$i]}" 2>/dev/null || echo 1)" in
      0) ;;
      1) total_failed=$((total_failed + 1)) ;;
      2) total_failed=$((total_failed + 1)) ;;
      3) total_xfail=$((total_xfail + 1)) ;;
    esac
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
compile-examples-stage1: (_compile-examples "build/compile_driver_bin_stage1" "examples/sentry_api.sprout examples/sentry_issue_browser.sprout examples/sentry_issue_browser_tui.sprout")

# Stage-2: emit IR → clang link for each example.
[group('examples')]
compile-examples-stage2: (_compile-examples "build/compile_driver_bin_stage2")

# Stage-3: emit IR → clang link for each example.
[group('examples')]
compile-examples-stage3: (_compile-examples "build/compile_driver_bin_stage3")

# ── Bootstrap & Seeds ─────────────────────────────────────────────────────────
#
# The committed seed is platform-agnostic LLVM IR text at bootstrap/compile_driver.ll.
# Bootstrap = link the IR with the runtime via clang.  No per-platform binaries.
# Master invariant: stage-1 built from the seed re-emits IR byte-identical to the
# seed for the current compile_driver.sprout — i.e. the seed is a fixed point of
# the compiler.  CI enforces this; refresh the seed with `just refresh-seed`
# whenever a compiler-source change perturbs the IR.

# Bootstrap compile_driver_bin_stage1 from the committed IR seed.
[group('bootstrap')]
bootstrap-from-seed:
  #!/usr/bin/env bash
  set -euo pipefail
  SEED="bootstrap/compile_driver.ll"
  if [[ ! -f "$SEED" ]]; then
    echo "ERROR: $SEED not found." >&2
    exit 1
  fi
  echo "==> Validating IR seed..."
  opt --passes=verify "$SEED" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$SEED" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{build_dir}}/compile_driver_bin_stage1"
  echo "==> Built {{build_dir}}/compile_driver_bin_stage1 from IR seed."

# Refresh bootstrap/compile_driver.ll from the current compile_driver.sprout source.
# Use this after any compiler-source change that perturbs the IR.  Reaches the
# new fixed point by iterating until two consecutive stages produce identical IR
# (typically one iteration after a codegen change).
[group('bootstrap')]
refresh-seed: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_refresh_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  ITER=0
  PREV="bootstrap/compile_driver.ll"
  while :; do
    ITER=$((ITER + 1))
    if (( ITER > 5 )); then
      echo "ERROR: did not converge to a fixed point after 5 iterations." >&2
      echo "       Likely cause: non-deterministic codegen." >&2
      exit 1
    fi
    NEXT="$TMPD/stage${ITER}.ll"
    echo "==> Iteration $ITER: emitting IR via stage-$ITER..."
    "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{driver}}" > "$NEXT"
    opt --passes=verify "$NEXT" -o /dev/null
    if cmp -s "$PREV" "$NEXT"; then
      echo "==> Fixed point reached at iteration $ITER."
      cp "$NEXT" bootstrap/compile_driver.ll
      echo "==> bootstrap/compile_driver.ll updated."
      break
    fi
    echo "    Diverges from previous; rebuilding stage from new IR..."
    clang "$NEXT" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{build_dir}}/compile_driver_bin_stage1"
    PREV="$NEXT"
  done

# Verify the committed seed is a fixed point: stage-1 built from the seed
# re-emits identical IR for the current driver source.  CI runs this on every PR.
[group('bootstrap')]
verify-bootstrap-fixed-point: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_fp_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Re-emitting IR via stage-1..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  if cmp -s bootstrap/compile_driver.ll "$TMP_LL"; then
    echo "==> Fixed point ✓ — bootstrap/compile_driver.ll matches stage-1 output."
  else
    echo "==> FIXED POINT BROKEN ✗" >&2
    echo "    bootstrap/compile_driver.ll diverges from current stage-1 output." >&2
    echo "    Run: just refresh-seed   (then stage the updated bootstrap/compile_driver.ll)" >&2
    exit 1
  fi

# ── DoD CI Gates ──────────────────────────────────────────────────────────────
#
# These recipes enforce DoD items #7–#10 — the checks that previously lived in
# pre-commit but were trimmed to keep that hook lightweight.  Run by CI on every
# PR; locally available for self-verification before commit.

# DoD #7 — smoke shapes.  Each tests/smoke_shapes/*.spr must emit IR cleanly,
# contain at least one `define` block, and contain no `str_concat(ptr null,…)`
# (null-pointer codegen regression guard).
[group('ci-checks')]
smoke-shapes: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_smk_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  failed=0
  for f in tests/smoke_shapes/*.spr; do
    [ -f "$f" ] || continue
    ir="$TMPD/$(basename "$f").ll"
    if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$f" > "$ir" 2>"$TMPD/err"; then
      echo "smoke-shapes: emit-IR failed for $f" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1)); continue
    fi
    if ! grep -q '^define ' "$ir"; then
      echo "smoke-shapes: $f produced IR with no 'define' block" >&2
      failed=$((failed + 1)); continue
    fi
    if grep -qE 'str_concat\(ptr null,|, ptr null\)' "$ir"; then
      echo "smoke-shapes: $f contains str_concat(ptr null,…) — null-ptr codegen regression" >&2
      failed=$((failed + 1))
    fi
  done
  if (( failed > 0 )); then
    echo "smoke-shapes: $failed shape(s) failed" >&2; exit 1
  fi
  echo "==> smoke-shapes ✓"

# DoD #8 — bundle smoke.  `--phase bundle` on token.sprout, ast.sprout, and
# prelude.sprout must produce non-empty output with no dot-prefix qualified names.
[group('ci-checks')]
bundle-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_bsm_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  failed=0
  for f in stdlib/compiler/token.sprout stdlib/compiler/ast.sprout stdlib/prelude.sprout; do
    [ -f "$f" ] || { echo "bundle-smoke: missing corpus file $f" >&2; failed=$((failed + 1)); continue; }
    out="$TMPD/$(basename "$f").out"
    if ! "{{build_dir}}/compile_driver_bin_stage1" --phase bundle "{{stdlib_root}}" "$f" > "$out" 2>"$TMPD/err"; then
      echo "bundle-smoke: --phase bundle failed for $f" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1)); continue
    fi
    # Extract qualified-name lines (skip "=== file ===" headers and "OK" lines).
    names=$(awk '/^=== .+ ===$/{past=1;next} past && /^OK$/{next} past && NF{print}' "$out")
    if [[ -z "$names" ]]; then
      echo "bundle-smoke: no qualified names emitted for $f" >&2
      failed=$((failed + 1)); continue
    fi
    dot_names=$(printf '%s\n' "$names" | grep '^\.' || true)
    if [[ -n "$dot_names" ]]; then
      echo "bundle-smoke: $f has dot-prefix qualified names:" >&2
      printf '%s\n' "$dot_names" >&2
      failed=$((failed + 1))
    fi
  done
  if (( failed > 0 )); then
    echo "bundle-smoke: $failed corpus file(s) failed" >&2; exit 1
  fi
  echo "==> bundle-smoke ✓"

# DoD #9 — APPROVED_BUILTINS guard.  Every non-static `long long <name>(` in
# runtime/sprout_runtime.c must be listed in runtime/APPROVED_BUILTINS.
# Per AGENTS.md "Builtin vs Stdlib" rules 4–6.
[group('ci-checks')]
check-approved-builtins:
  #!/usr/bin/env bash
  set -euo pipefail
  APPROVED=runtime/APPROVED_BUILTINS
  SOURCE=runtime/sprout_runtime.c
  if [[ ! -f "$APPROVED" ]] || [[ ! -f "$SOURCE" ]]; then
    echo "check-approved-builtins: missing $APPROVED or $SOURCE" >&2; exit 1
  fi
  # Names declared in runtime.c (excluding `static long long`).
  declared=$(grep -E '^long long [a-z_][a-zA-Z0-9_]*\(' "$SOURCE" | sed -E 's/^long long ([a-z_][a-zA-Z0-9_]*)\(.*/\1/' | sort -u)
  # Names listed in APPROVED_BUILTINS (strip comments and whitespace).
  approved=$(sed -E 's/#.*$//; s/^[[:space:]]+|[[:space:]]+$//g' "$APPROVED" | grep -v '^$' | sort -u)
  missing=$(comm -23 <(echo "$declared") <(echo "$approved") || true)
  if [[ -n "$missing" ]]; then
    echo "check-approved-builtins: builtins in $SOURCE missing from $APPROVED:" >&2
    printf '  %s\n' $missing >&2
    echo >&2
    echo "  Per AGENTS.md 'Builtin vs Stdlib' rules 4-6: add each name to" >&2
    echo "  $APPROVED with an inline comment explaining why it cannot be" >&2
    echo "  done in Sprout." >&2
    exit 1
  fi
  echo "==> check-approved-builtins ✓"

# DoD #10 — example canary RUN.  The canary set must compile AND run to
# completion without crashing.  `just compile-examples-stage1` only covers
# compile; this recipe adds the runtime check.
[group('ci-checks')]
run-example-canary: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_canary_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/rt.o" 2>"$TMPD/rt.err" \
    || { echo "run-example-canary: runtime compile failed" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  failed=0
  for f in examples/tuples.sprout examples/factorial.sprout examples/maybe_map.sprout examples/typeclass_collections_demo.sprout examples/fizzbuzz.sprout; do
    [ -f "$f" ] || { echo "run-example-canary: missing $f" >&2; failed=$((failed + 1)); continue; }
    name=$(basename "$f" .sprout)
    ll="$TMPD/$name.ll"
    bin="$TMPD/$name.bin"
    if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$f" > "$ll" 2>"$TMPD/err"; then
      echo "run-example-canary: emit-IR failed for $f" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1)); continue
    fi
    if ! clang "$ll" "$TMPD/rt.o" {{clang_extra}} -o "$bin" 2>"$TMPD/err"; then
      echo "run-example-canary: link failed for $f" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1)); continue
    fi
    if ! "$bin" > /dev/null 2>"$TMPD/err"; then
      echo "run-example-canary: $f crashed at runtime (exit $?)" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1))
    fi
  done
  if (( failed > 0 )); then
    echo "run-example-canary: $failed canary(s) failed" >&2; exit 1
  fi
  echo "==> run-example-canary ✓"

# ── REPL ──────────────────────────────────────────────────────────────────────

# Build sproutd — combined REPL + analysis service binary (self-configuring).
[group('build')]
build-sproutd:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2; exit 1
  fi
  TMP_LL="/tmp/sprout_sproutd_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for sproutd..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/sproutd_driver.sprout" > "$TMP_LL"
  echo "==> Validating IR..."
  opt --passes=verify "$TMP_LL" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{build_dir}}/sproutd"
  echo "==> Built {{build_dir}}/sproutd"

# ── Analysis Service ──────────────────────────────────────────────────────────

# Build the analysis service binary. Prefers stage-2; falls back to stage-1.
[group('service')]
build-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ -x "{{build_dir}}/compile_driver_bin_stage2" ]]; then
    STAGE="{{build_dir}}/compile_driver_bin_stage2"
  elif [[ -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    STAGE="{{build_dir}}/compile_driver_bin_stage1"
  else
    echo "ERROR: neither compile_driver_bin_stage2 nor compile_driver_bin_stage1 found" >&2; exit 1
  fi
  echo "==> Using compiler: $STAGE"
  TMP_LL="/tmp/sprout_analysis_service_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for analysis service..."
  "$STAGE" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/analysis_service_main.sprout" > "$TMP_LL"
  echo "==> Validating IR..."
  opt --passes=verify "$TMP_LL" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "{{build_dir}}/analysis_service_bin"
  echo "==> Built {{build_dir}}/analysis_service_bin"

# Run the analysis service in foreground (reads JSON from stdin, writes to stdout).
# Example: echo '{"op":"declared_names_in_source","module_source":"fn foo() -> Int = 1"}' | just run-analysis-service
[group('service')]
run-analysis-service:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/analysis_service_bin" ]]; then
    echo "ERROR: analysis_service_bin not found; run: just build-analysis-service" >&2; exit 1
  fi
  exec "{{build_dir}}/analysis_service_bin" "{{stdlib_root}}"
