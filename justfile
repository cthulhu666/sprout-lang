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
#
# All fmt/lint recipes depend on `build-fmt-from-seed`, which has its own
# no-op guard. This makes `just fmt` self-sufficient: it never runs against
# a stale fmt_bin, and the common case (everything fresh) costs only stat
# calls. Without this dependency chain, running `just fmt` with a stale
# fmt_bin silently produces obsolete formatting — exactly the bug that
# broke PR #19's CI (2026-06-10): test files formatted with no-space
# `deriving(...)` locally vs CI's fresh `deriving (...)`.

[group('fmt')]
fmt: build-fmt-from-seed
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" fmt

[group('fmt')]
fmt-check: build-fmt-from-seed
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" fmt --check

[group('fmt')]
fmt-file file: build-fmt-from-seed
  "{{build_dir}}/fmt_bin" fmt {{quote(file)}}

[group('fmt')]
fmt-check-file file: build-fmt-from-seed
  "{{build_dir}}/fmt_bin" fmt --check {{quote(file)}}

[group('fmt')]
lint: build-fmt-from-seed
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 "{{build_dir}}/fmt_bin" lint

[group('fmt')]
lint-file file: build-fmt-from-seed
  "{{build_dir}}/fmt_bin" lint {{quote(file)}}

# Build fmt_bin via stage-1 (which is built from the IR seed).  fmt_bin chains
# off compile_driver_bin_stage1 — no platform-specific binary required.
#
# No-op guard: skips rebuild when fmt_bin is newer than stage-1 binary, the
# seed, fmt_driver source, and formatter source. Mirrors the bootstrap-from-seed
# guard pattern so `just fmt` is cheap in the common case but always fresh.
[group('fmt')]
build-fmt-from-seed: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  OUT="{{build_dir}}/fmt_bin"
  STAGE1="{{build_dir}}/compile_driver_bin_stage1"
  SEED="bootstrap/compile_driver.ll"
  FMT_DRIVER="{{stdlib_root}}/compiler/fmt_driver.sprout"
  FORMATTER="{{stdlib_root}}/compiler/formatter.sprout"
  # Freshness check: skip rebuild if fmt_bin is newer than every input it
  # transitively depends on. The four checks cover: compiler changes
  # (STAGE1, SEED), formatter rule changes (FORMATTER), driver wiring
  # (FMT_DRIVER). Misses subtle prelude.sprout changes affecting fmt; the
  # user can force a rebuild with `rm build/fmt_bin && just build-fmt-from-seed`.
  if [[ -x "$OUT" && "$OUT" -nt "$STAGE1" && "$OUT" -nt "$SEED" && "$OUT" -nt "$FMT_DRIVER" && "$OUT" -nt "$FORMATTER" ]]; then
    echo "==> fmt_bin is up-to-date with stage-1 + seed + formatter sources; skipping rebuild."
    exit 0
  fi
  TMP_LL="/tmp/sprout_fmt_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for fmt_bin via stage-1..."
  "$STAGE1" --emit-ir "{{stdlib_root}}" "$FMT_DRIVER" > "$TMP_LL"
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$OUT"
  echo "==> Built $OUT"

# ── Iface (precompiled module interfaces) ────────────────────────────────────
#
# Phase 1.x of docs/iface-precompiled-modules-v1-draft.md.  Each stdlib module
# emits a build/iface/<qualified-name>.iface artifact: an S-expression
# listing every exported scheme.  No consumer yet wires these into the bundler
# (that's Phase 2); this recipe just exercises the producer side end-to-end.

# Compile every stdlib module to its .iface artifact under build/iface/.
[group('iface')]
refresh-iface: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE1="{{build_dir}}/compile_driver_bin_stage1"
  OUT_DIR="{{build_dir}}/iface"
  mkdir -p "$OUT_DIR"
  count=0
  while IFS= read -r -d '' f; do
    # Convert path -> qualified module name (strip .sprout, / -> .).
    module_name=$(echo "$f" | sed 's,\.sprout$,,; s,/,.,g')
    out="$OUT_DIR/$module_name.iface"
    "$STAGE1" --emit-iface "{{stdlib_root}}" "$module_name" "$f" > "$out" 2>/dev/null \
      || { echo "ERROR: failed to emit iface for $f" >&2; rm -f "$out"; exit 1; }
    count=$((count + 1))
  done < <(find stdlib -name '*.sprout' -type f -print0)
  echo "==> Emitted $count iface(s) to $OUT_DIR/"

# Validate every cached .iface file under build/iface/ via the decoder.
[group('iface')]
check-iface-all: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE1="{{build_dir}}/compile_driver_bin_stage1"
  IFACE_DIR="{{build_dir}}/iface"
  if [[ ! -d "$IFACE_DIR" ]]; then
    echo "ERROR: $IFACE_DIR not found; run: just refresh-iface" >&2; exit 1
  fi
  count=0
  ok_count=0
  while IFS= read -r -d '' f; do
    count=$((count + 1))
    if "$STAGE1" --check-iface "$f" 2>&1 | grep -q "^OK:"; then
      ok_count=$((ok_count + 1))
    else
      echo "FAIL: $f"
    fi
  done < <(find "$IFACE_DIR" -name '*.iface' -type f -print0)
  echo "==> $ok_count / $count ifaces validated"
  if [[ "$ok_count" -ne "$count" ]]; then exit 1; fi

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

# Build {{file}} with GC profiling compiled in (-DSPROUT_GC_PROFILE) and run it
# with SPROUT_GC_PROFILE=1, printing a "[gc profile] ..." summary to stderr at
# exit: find_managed_ptr calls/hops, avg hash-probe length, drain edges, sweep
# visits, mark-root slots, and total GC microseconds. The hot-path counters are
# compile-time gated, so a normal `just run` build is byte-identical (no cost).
[group('dev')]
gc-profile file:
  #!/usr/bin/env bash
  set -euo pipefail
  if [[ ! -x "{{build_dir}}/compile_driver_bin_stage1" ]]; then
    echo "ERROR: compile_driver_bin_stage1 not found; run: just bootstrap-from-seed" >&2
    exit 1
  fi
  TMP_LL="/tmp/sprout_gcprof_$$.ll"
  TMP_BIN="/tmp/sprout_gcprof_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" runtime/sprout_runtime.c -O2 -DSPROUT_GC_PROFILE {{clang_extra}} -o "$TMP_BIN"
  SPROUT_GC_PROFILE=1 "$TMP_BIN"

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
test: test-stdlib-stage1 test-type-errors

[private]
_test-stdlib stage:
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
  JOBS=$(( NCPU > 8 ? 8 : NCPU ))
  TMPD=$(mktemp -d /tmp/sprout_tests_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  # Pre-compile the runtime once; each test links the resulting .o.
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/rt.o" 2>"$TMPD/rt.err" \
    || { echo "ERROR: runtime compile failed"; cat "$TMPD/rt.err"; exit 1; }
  declare -a files=()
  declare -a outs=()
  declare -a stats=()
  for dir in tests/stdlib tests/stdlib/compiler; do
    [ -d "$dir" ] || continue
    for f in "$dir"/*.spr "$dir"/*.sprout; do
      [ -f "$f" ] || continue
      files+=("$f")
    done
  done
  declare -a pids=()
  idx=0
  active=0
  for f in "${files[@]}"; do
    outs+=("$TMPD/$idx.out")
    stats+=("$TMPD/$idx.st")
    (
      set +e
      echo "==> $f" > "$TMPD/$idx.out"
      ok=1
      "./$STAGE" --emit-ir "{{stdlib_root}}" "$f" > "$TMPD/$idx.ll" 2>"$TMPD/$idx.err"
      if [[ $? -ne 0 ]]; then
        { echo "  COMPILE FAILED:"; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      elif ! opt --passes=verify "$TMPD/$idx.ll" -o /dev/null 2>"$TMPD/$idx.err"; then
        { echo "  IR INVALID (opt --passes=verify):"; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      else
        clang "$TMPD/$idx.ll" "$TMPD/rt.o" {{clang_extra}} -o "$TMPD/$idx.bin" 2>"$TMPD/$idx.err"
        if [[ $? -ne 0 ]]; then
          { echo "  LINK FAILED:"; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
        fi
      fi
      if [[ $ok -eq 1 ]]; then
        if out=$(SPROUT_STDLIB_ROOT="{{stdlib_root}}" "$TMPD/$idx.bin" 2>&1); then
          echo "$out" >> "$TMPD/$idx.out"
          if echo "$out" | grep -q "^SUITE FAILED"; then
            echo 1 > "$TMPD/$idx.st"
          else
            echo 0 > "$TMPD/$idx.st"
          fi
        else
          status=$?
          { echo "$out"; echo "  RUN FAILED: exit $status"; } >> "$TMPD/$idx.out"
          echo 1 > "$TMPD/$idx.st"
        fi
      else
        echo 1 > "$TMPD/$idx.st"
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
  for i in "${!outs[@]}"; do
    cat "${outs[$i]}" 2>/dev/null || true
    case "$(cat "${stats[$i]}" 2>/dev/null || echo 1)" in
      0) ;;
      *) total_failed=$((total_failed + 1)) ;;
    esac
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
# Known xfail: sentry_issue_browser{,_tui} (import examples.* unresolved).
# (sentry_api now compiles under typed codegen — removed from xfail.)
[group('examples')]
compile-examples-stage1: (_compile-examples "build/compile_driver_bin_stage1" "examples/sentry_issue_browser.sprout examples/sentry_issue_browser_tui.sprout")

# Negative type-checking conformance: each tests/conformance/type_error/<n>.spr must
# be rejected by `--phase check` with output containing the substring in <n>.err.
# (`--phase check` exits 0 even on type errors, so matching is by output content.)
# xfail = fixtures whose expected diagnostic is not yet produced (tracked TODO).
_test-type-errors stage xfail="":
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  XFAIL="{{xfail}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  total_failed=0
  total_xfail=0
  for spr in tests/conformance/type_error/*.spr; do
    [ -f "$spr" ] || continue
    name="$(basename "${spr%.spr}")"
    err="tests/conformance/type_error/$name.err"
    if [[ ! -f "$err" ]]; then
      echo "==> $name"; echo "  MISSING .err"; total_failed=$((total_failed + 1)); continue
    fi
    is_xfail=0
    for xf in $XFAIL; do [[ "$name" == "$xf" ]] && is_xfail=1 && break; done
    expected="$(cat "$err")"
    out="$("./$STAGE" --phase check "{{stdlib_root}}" "$spr" 2>&1 || true)"
    echo "==> $name"
    if echo "$out" | grep -qF -- "$expected"; then
      if [[ $is_xfail -eq 1 ]]; then echo "  UNEXPECTED MATCH (remove from xfail)"; total_failed=$((total_failed + 1))
      else echo "  OK (rejected)"; fi
    else
      if [[ $is_xfail -eq 1 ]]; then echo "  xfail (expected diagnostic not yet produced)"; total_xfail=$((total_xfail + 1))
      else echo "  FAILED: expected output to contain: $expected"; total_failed=$((total_failed + 1)); fi
    fi
  done
  echo ""
  [[ $total_xfail -gt 0 ]] && echo "==> $total_xfail type-error fixture(s) xfail (expected)"
  if [ "$total_failed" -gt 0 ]; then
    echo "==> $total_failed type-error fixture(s) FAILED"
    exit 1
  fi
  echo "==> All type-error fixtures rejected as expected"

# Stage-1 negative type-checking gate. No xfail — every fixture is expected to
# be rejected with its diagnostic. (Overlapping-instance and do-block
# family-conflict diagnostics landed in PR-3; missing_nested_instance{,_maybe}
# via the resolve pass in #110.)
[group('test')]
test-type-errors: (_test-type-errors "build/compile_driver_bin_stage1" "")

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
  RUNTIME="runtime/sprout_runtime.c"
  OUT="{{build_dir}}/compile_driver_bin_stage1"
  if [[ ! -f "$SEED" ]]; then
    echo "ERROR: $SEED not found." >&2
    exit 1
  fi
  # No-op guard: if stage-1 binary is already up-to-date with seed + runtime,
  # skip the rebuild. CI steps each invoke `just bootstrap-from-seed` as a
  # `just` dependency in a fresh process, so just's dedupe doesn't apply —
  # without this guard the bootstrap runs 5+ times per CI run.
  if [[ -x "$OUT" && "$OUT" -nt "$SEED" && "$OUT" -nt "$RUNTIME" ]]; then
    echo "==> Stage-1 binary is up-to-date with seed + runtime; skipping bootstrap."
    exit 0
  fi
  echo "==> Validating IR seed..."
  opt --passes=verify "$SEED" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$SEED" "$RUNTIME" -O2 {{clang_extra}} -o "$OUT"
  echo "==> Built $OUT from IR seed."

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
      FP=$(find stdlib/compiler -name "*.sprout" | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -d' ' -f1)
      { echo "; seed-fingerprint: $FP"; cat "$NEXT"; } > bootstrap/compile_driver.ll
      echo "==> bootstrap/compile_driver.ll updated (fingerprint: $FP)."
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
  # The fingerprint header is written only by refresh-seed; stage-1 output omits it.
  if cmp -s <(tail -n +2 bootstrap/compile_driver.ll) "$TMP_LL"; then
    echo "==> Fixed point ✓ — bootstrap/compile_driver.ll matches stage-1 output."
  else
    echo "==> FIXED POINT BROKEN ✗" >&2
    echo "    bootstrap/compile_driver.ll diverges from current stage-1 output." >&2
    echo "    Run: just refresh-seed   (then stage the updated bootstrap/compile_driver.ll)" >&2
    exit 1
  fi

# Instant seed-freshness check — no compilation required.
# Reads the '; seed-fingerprint: <hash>' comment embedded in line 1 of
# bootstrap/compile_driver.ll (written by refresh-seed) and compares it
# against a freshly computed hash of all stdlib/compiler/*.sprout sources.
[group('bootstrap')]
seed-stale:
  #!/usr/bin/env bash
  set -euo pipefail
  CURRENT=$(find stdlib/compiler -name "*.sprout" | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -d' ' -f1)
  FIRST=$(head -1 bootstrap/compile_driver.ll)
  if [[ "$FIRST" != "; seed-fingerprint: "* ]]; then
    echo "==> SEED STALE ✗ — no fingerprint comment found in bootstrap/compile_driver.ll." >&2
    echo "    Run: just refresh-seed   (then stage the updated bootstrap/compile_driver.ll)" >&2
    exit 1
  fi
  STORED="${FIRST#; seed-fingerprint: }"
  if [[ "$CURRENT" == "$STORED" ]]; then
    echo "==> Seed fingerprint ✓ — compiler sources unchanged since last refresh-seed."
  else
    echo "==> SEED STALE ✗ — compiler sources changed since last refresh-seed." >&2
    echo "    Run: just refresh-seed   (then stage the updated bootstrap/compile_driver.ll)" >&2
    exit 1
  fi

# ── Diagnostic Tools ──────────────────────────────────────────────────────────

# Map an opt/clang error line number back to its enclosing Sprout function.
# When opt --passes=verify fails with "error at line N of stage2.ll", run:
#   just llvm-where build/stage2.ll N
# See docs/debugging.md §llvm-where for full context.
[group('dev')]
llvm-where ll_file line:
  bash scripts/llvm_diag.sh "{{ll_file}}" "{{line}}"

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

# Loud-fail guard.  A self-contained (importless) file that calls a
# non-intrinsic prelude name must FAIL to compile with a clear "unresolved
# call" error, NOT silently emit zero_val (`ret i64 0`).  Regression for the
# codegen.emit_named_call silent fallback that disguised bundler/iface gaps as
# GC/typeclass/print bugs.
[group('ci-checks')]
loud-fail-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_lfs_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  printf 'fn main() -> Unit !{IO} =\n  print(int_to_string(5))\n' > "$TMPD/unresolved.spr"
  if "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$TMPD/unresolved.spr" > "$TMPD/out.ll" 2>"$TMPD/err"; then
    echo "loud-fail-smoke: importless int_to_string call compiled silently (expected a hard 'unresolved call' error)" >&2
    exit 1
  fi
  if ! grep -q "unresolved call" "$TMPD/err"; then
    echo "loud-fail-smoke: compile failed but without the expected 'unresolved call' message:" >&2
    cat "$TMPD/err" >&2; exit 1
  fi
  echo "==> loud-fail-smoke ✓"

# Typed-codegen argv gate.  The typed `main` shim (ir_lowering.main_shim) must
# call @sprout_set_argv(argc, argv) so a typed-built binary's argv_all() sees
# its command-line arguments — the typed-codegen flip self-compiles the
# compiler, whose main() reads argv.  The parity corpus runs every binary with
# NO args, so this is the ONLY gate exercising argv_all() under typed codegen.
[group('ci-checks')]
argv-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_argv_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/argv_smoke/argv_echo.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/err"; then
    echo "argv-smoke: typed emit failed" >&2; cat "$TMPD/err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "argv-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  got=$("$TMPD/bin" ping hello)
  if [[ "$got" != "pong:hello" ]]; then
    echo "argv-smoke: typed-built binary mishandled argv — expected 'pong:hello', got '$got'" >&2
    echo "  (typed main shim likely missing @sprout_set_argv; see ir_lowering.main_shim)" >&2
    exit 1
  fi
  echo "==> argv-smoke ✓"

# Flip smoke: verifies `--emit-ir` routes through TYPED codegen (the flip) by
# asserting its output is byte-identical to `--use-ir-codegen`, and that the
# `--use-direct-codegen` escape hatch still reaches the direct backend (valid,
# non-empty IR that DIFFERS from typed). RED until the flip lands in the seed.
flip-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_flipsmoke_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  BIN="{{build_dir}}/compile_driver_bin_stage1"
  FIX=tests/flip_smoke/flip_fixture.spr
  "$BIN" --emit-ir        "{{stdlib_root}}" "$FIX" > "$TMPD/emit.ll"   2>"$TMPD/emit.err"
  "$BIN" --use-ir-codegen "{{stdlib_root}}" "$FIX" > "$TMPD/typed.ll"  2>"$TMPD/typed.err"
  if ! "$BIN" --use-direct-codegen "{{stdlib_root}}" "$FIX" > "$TMPD/direct.ll" 2>"$TMPD/direct.err"; then
    echo "flip-smoke: --use-direct-codegen failed (escape hatch missing?)" >&2; cat "$TMPD/direct.err" >&2; exit 1
  fi
  if ! cmp -s "$TMPD/emit.ll" "$TMPD/typed.ll"; then
    echo "flip-smoke: --emit-ir is NOT routing through typed codegen (differs from --use-ir-codegen)." >&2
    echo "  -> the flip is not in the seed; refresh-seed after the dispatcher reroute." >&2
    exit 1
  fi
  if [ ! -s "$TMPD/direct.ll" ] || ! grep -q '^define ' "$TMPD/direct.ll"; then
    echo "flip-smoke: --use-direct-codegen produced no real IR." >&2; exit 1
  fi
  if cmp -s "$TMPD/emit.ll" "$TMPD/direct.ll"; then
    echo "flip-smoke: --use-direct-codegen output equals typed — escape hatch not reaching direct codegen." >&2
    exit 1
  fi
  echo "==> flip-smoke ✓ (--emit-ir == typed; --use-direct-codegen reaches direct)"

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

# Stack-overflow diagnostic regression. A deeply (non-tail) recursive program
# overflows the native stack; the runtime must catch it on its alternate signal
# stack and panic cleanly ("stack overflow" + a backtrace) instead of dying with
# a bare, silent SIGSEGV. RED before the sigaltstack handler (empty stderr,
# exit 139); GREEN after. -rdynamic (Linux only) makes the backtrace frames
# named rather than bare addresses; macOS symbolises from the symbol table.
stack-overflow-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_sov_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/stack_overflow_smoke/deep_recursion.spr
  RDYN=""; [ "$(uname)" != "Darwin" ] && RDYN="-rdynamic"
  if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "stack-overflow-smoke: emit-IR failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" runtime/sprout_runtime.c -O2 $RDYN {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "stack-overflow-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  set +e
  "$TMPD/bin" > "$TMPD/run.out" 2>"$TMPD/run.err"
  ec=$?
  set -e
  if [ "$ec" -eq 0 ]; then
    echo "stack-overflow-smoke: fixture did NOT overflow (exit 0) — the optimizer likely folded the recursion; make it deeper/less foldable" >&2
    exit 1
  fi
  if ! grep -q "stack overflow" "$TMPD/run.err"; then
    echo "stack-overflow-smoke: overflow was not reported cleanly (exit $ec); expected 'stack overflow' on stderr" >&2
    echo "  (runtime crash handler likely missing the sigaltstack/SA_ONSTACK path — see sprout_install_crash_handlers)" >&2
    echo "--- stderr was ---" >&2; cat "$TMPD/run.err" >&2
    exit 1
  fi
  echo "==> stack-overflow-smoke ✓ (clean panic, exit $ec)"

# W7/F-DIV division-by-zero guard regression. The fixture divides by a RUNTIME
# zero (`10 / list_length(argv)` with no args), which neither the compiler nor
# clang can fold. A bare `sdiv i64 _, 0` is LLVM undefined behavior; the emitted
# guard must panic cleanly ("division by zero", non-zero exit). RED before the
# ast_to_ir guard (UB — often exit 0 or garbage), GREEN after.
div-by-zero-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_divz_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/div_smoke/div_by_zero.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "div-by-zero-smoke: emit-IR failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "div-by-zero-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  set +e
  "$TMPD/bin" > "$TMPD/run.out" 2>"$TMPD/run.err"
  ec=$?
  set -e
  if [ "$ec" -eq 0 ]; then
    echo "div-by-zero-smoke: 10/0 did NOT panic (exit 0) — the guard is missing or was optimized away" >&2
    exit 1
  fi
  if ! grep -q "division by zero" "$TMPD/run.err"; then
    echo "div-by-zero-smoke: not reported cleanly (exit $ec); expected 'division by zero' on stderr" >&2
    echo "--- stderr was ---" >&2; cat "$TMPD/run.err" >&2
    exit 1
  fi
  echo "==> div-by-zero-smoke ✓ (clean panic, exit $ec)"

# TCO differential: typed codegen (--use-ir-codegen) must emit at least as many
# tail-call-optimization loops as direct codegen (--emit-ir) for the same source.
# A self-tail-recursive function that direct codegen loops but typed codegen does
# NOT becomes one native frame per iteration — fine on small inputs, a stack
# overflow at scale (e.g. the compiler's own lexer.tokenize_from: flip blocker #2).
# The parity corpus can't see this (all inputs are small); this can. Counts the
# `tco_loop` basic-block labels direct codegen emits and asserts typed emits no
# fewer. Diagnostic + progress meter for the typed-codegen TCO work.
tco-diff PROBE="tests/stack_overflow_smoke/deep_recursion.spr": bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_tco_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  BIN="{{build_dir}}/compile_driver_bin_stage1"
  if ! "$BIN" --use-direct-codegen "{{stdlib_root}}" "{{PROBE}}" > "$TMPD/direct.ll" 2>"$TMPD/d.err"; then
    echo "tco-diff: direct emit failed for {{PROBE}}" >&2; cat "$TMPD/d.err" >&2; exit 1
  fi
  if ! "$BIN" --use-ir-codegen "{{stdlib_root}}" "{{PROBE}}" > "$TMPD/typed.ll"  2>"$TMPD/t.err"; then
    echo "tco-diff: typed emit failed for {{PROBE}}" >&2; cat "$TMPD/t.err" >&2; exit 1
  fi
  d=$(grep -cE '^tco_loop' "$TMPD/direct.ll" || true)
  t=$(grep -cE '^tco_loop' "$TMPD/typed.ll"  || true)
  echo "tco-diff {{PROBE}}: direct=$d typed=$t"
  if [ "$t" -lt "$d" ]; then
    echo "tco-diff: REGRESSION — typed codegen dropped $((d - t)) TCO loop(s)." >&2
    echo "  Self-tail-recursive functions will overflow the stack at scale; see flip blocker #2 (lexer.tokenize_from)." >&2
    exit 1
  fi
  echo "==> tco-diff ✓ (typed >= direct TCO loops)"

# TCO runtime regression: a deep tail-recursive program must run to completion
# under typed codegen (--use-ir-codegen), not just direct codegen. The fixture
# carries a heap param rooted across the recursive call, so a non-TCO'd typed
# build either exhausts the GC root pool or overflows the stack (the failure
# WITHOUT a per-iteration root reset on the back-edge). Direct codegen already
# TCOs it. RED until typed-codegen TCO lands (blocker #2); GREEN after.
tco-runtime-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_tcort_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/stack_overflow_smoke/deep_tail_recursion.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "tco-runtime-smoke: typed emit failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "tco-runtime-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  set +e
  got=$("$TMPD/bin" 2>"$TMPD/run.err"); ec=$?
  set -e 2>/dev/null || true
  if [ "$ec" -ne 0 ] || [ "$got" != "1" ]; then
    echo "tco-runtime-smoke: deep tail recursion failed under typed codegen (exit $ec, output '$got', expected '1')" >&2
    echo "  -> typed codegen is not TCO-ing self-tail-recursion (or not resetting roots on the back-edge); see blocker #2." >&2
    echo "--- stderr ---" >&2; head -c 400 "$TMPD/run.err" >&2 || true; echo "" >&2
    exit 1
  fi
  echo "==> tco-runtime-smoke ✓ (deep tail recursion completes under typed codegen)"

# Flip-readiness dry-run: the real gate for making typed codegen the default.
# Parity (ir_runtime_parity.sh) is necessary but NOT sufficient — it runs only
# small corpus files with no argv. The compiler self-compiling exercises argv,
# deep recursion, and the whole language at once, which is where both flip
# blockers surfaced. This drives the canonical sequence: typed self-compile the
# compiler -> verify -> link -> have THAT binary self-compile to a fixed point.
# RED until BOTH the argv fix (#95) is in the seed AND blocker #2 (typed codegen
# does no TCO) is fixed: without #95 the typed-built compiler sees empty argv and
# prints usage; with #95 it then stack-overflows self-compiling. The new
# stack-overflow panic names the culprit when it fails. Make this a hard CI gate
# once it goes green.
flip-readiness: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_flip_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  BIN="{{build_dir}}/compile_driver_bin_stage1"
  COMPILER=stdlib/compiler/compile_driver.sprout
  echo "== [1/4] typed self-compile of the compiler =="
  if ! "$BIN" --use-ir-codegen "{{stdlib_root}}" "$COMPILER" > "$TMPD/typed_self.ll" 2>"$TMPD/emit.err"; then
    echo "flip-readiness: FAIL [1/4] typed self-compile emit failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  echo "   ok ($(wc -c <"$TMPD/typed_self.ll" | tr -d ' ') bytes)"
  echo "== [2/4] opt --passes=verify =="
  if ! opt --passes=verify "$TMPD/typed_self.ll" -o /dev/null 2>"$TMPD/verify.err"; then
    echo "flip-readiness: FAIL [2/4] typed IR failed verify" >&2; cat "$TMPD/verify.err" >&2; exit 1
  fi
  echo "   ok"
  echo "== [3/4] link typed-built compiler =="
  if ! clang "$TMPD/typed_self.ll" runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/stage2_typed" 2>"$TMPD/link.err"; then
    echo "flip-readiness: FAIL [3/4] link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  echo "   ok"
  echo "== [4/4] FIXED POINT — typed-built compiler self-compiles the compiler =="
  set +e
  "$TMPD/stage2_typed" --emit-ir "{{stdlib_root}}" "$COMPILER" > "$TMPD/fp.ll" 2>"$TMPD/fp.err"
  ec=$?
  set -e 2>/dev/null || true
  fpsize=$(wc -c <"$TMPD/fp.ll" | tr -d ' ')
  # The output must be REAL IR, not a usage/error message: a non-empty exit-0
  # stdout is not enough (the argv blocker prints a ~240-byte usage string with
  # exit 0). Require it to verify as LLVM IR, define functions, and be large.
  real_ir=1
  [ "$ec" -ne 0 ] && real_ir=0
  [ "$fpsize" -lt 100000 ] && real_ir=0
  grep -q '^define ' "$TMPD/fp.ll" || real_ir=0
  opt --passes=verify "$TMPD/fp.ll" -o /dev/null 2>/dev/null || real_ir=0
  if [ "$real_ir" -ne 1 ]; then
    echo "flip-readiness: FAIL [4/4] typed-built compiler did not self-compile to real IR (exit $ec, $fpsize bytes)" >&2
    if grep -q '^ERROR: usage' "$TMPD/fp.ll"; then
      echo "  -> typed-built compiler printed USAGE: empty argv (blocker #1, needs the #95 argv fix in the seed)." >&2
    fi
    echo "--- its stderr (the stack-overflow panic, if any, names the culprit) ---" >&2
    head -c 1200 "$TMPD/fp.err" >&2 || true
    echo "" >&2
    echo "--- its stdout head ---" >&2
    head -c 400 "$TMPD/fp.ll" >&2 || true
    echo "" >&2
    echo "This is the flip gate. NOT ready until this step emits verifiable IR." >&2
    exit 1
  fi
  echo "   ok ($fpsize bytes, verifies) — FLIP READY ✓"

# GC-stress pass (P11-2e lessons): run a curated set of rooting-exercising
# typed-codegen tests under SPROUT_GC_STRESS=1 (collect on EVERY allocation).
# The default-threshold suite hides use-after-free rooting bugs as false greens;
# stress collapses the timing window and fails loudly.  This is the durable
# guard for the whole typed-codegen rooting class.  See project_gc_stress_oracle.
# Grow STRESS_FILES as typed-codegen coverage warrants.
test-stress: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_stress_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  clang -c runtime/sprout_runtime.c -O2 {{clang_extra}} -o "$TMPD/rt.o" 2>"$TMPD/rt.err" \
    || { echo "test-stress: runtime compile failed" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  # Gated (must pass under stress).  ctors/match/closures promoted here once the
  # PR 11 item 4 GC-UAF was fixed (ir_rooting: IRCall now roots its heap operands
  # across the call; @ref_new and other builtins may collect before consuming an
  # operand).  All three were the same class — they presented differently (tag-
  # read abort vs EXC_BAD_ACCESS) only by how the swept address was reused.
  STRESS_FILES="tests/stdlib/test_ir_rooting.spr tests/stdlib/test_ir_codegen_ctors.spr tests/stdlib/test_ir_codegen_match.spr tests/stdlib/test_ir_codegen_closures.spr tests/stdlib/test_ir_codegen_char_rooting.spr tests/stdlib/test_stress_global_roots.spr"
  # Known-failing under stress — false-green at the default threshold, FOUND BY
  # THIS PASS (residual typed-codegen rooting UAF, GC-confirmed via
  # SPROUT_GC_DISABLE).  Tracked in BACKLOG.md; warn-only here.  Promote to
  # STRESS_FILES as each is fixed (an UNEXPECTED PASS flags that it's ready).
  STRESS_XFAIL=""
  failed=0
  run_one() {  # prints "ok" or "fail"; never exits
    local f="$1" name ll bin out
    name=$(basename "$f" .spr); ll="$TMPD/$name.ll"; bin="$TMPD/$name.bin"
    "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$f" > "$ll" 2>"$TMPD/err" || { echo fail; return; }
    clang "$ll" "$TMPD/rt.o" {{clang_extra}} -o "$bin" 2>"$TMPD/err" || { echo fail; return; }
    if out=$(SPROUT_GC_STRESS=1 "$bin" 2>&1); then
      echo "$out" | grep -q "SUITE FAILED" && echo fail || echo ok
    else
      echo fail
    fi
  }
  for f in $STRESS_FILES; do
    [ -f "$f" ] || { echo "test-stress: missing $f" >&2; failed=$((failed + 1)); continue; }
    if [[ "$(run_one "$f")" == ok ]]; then
      echo "  PASS (stress): $f"
    else
      echo "test-stress: $f FAILED under SPROUT_GC_STRESS=1" >&2; failed=$((failed + 1))
    fi
  done
  for f in $STRESS_XFAIL; do
    [ -f "$f" ] || continue
    if [[ "$(run_one "$f")" == ok ]]; then
      echo "  UNEXPECTED PASS (stress) — promote to STRESS_FILES: $f"
    else
      echo "  xfail (stress, tracked): $f"
    fi
  done
  if (( failed > 0 )); then
    echo "test-stress: $failed gated file(s) failed under SPROUT_GC_STRESS=1" >&2; exit 1
  fi
  echo "==> test-stress ✓"

# GC use-after-free free-tracer (P11-2e diagnostic).  Compiles <file> via typed
# codegen with debug info, runs it under lldb + SPROUT_GC_STRESS=1, and stops the
# instant <watch_fn> is entered with a pointer arg (x0) that was already freed,
# printing the victim's full alloc/free lineage.  <watch_fn> MUST be a function
# that RECEIVES the suspected victim as its first argument — find it from the
# crash's abort backtrace first (e.g. the match-dispatch fn that reads the
# corrupted scrutinee), NOT the arg-less sprout_abort_match.  See scripts/gc_free_trace.py.
gc-trace file watch_fn: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_gctrace_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "{{file}}" > "$TMPD/t.ll" 2>"$TMPD/err" \
    || { echo "gc-trace: typed emit failed for {{file}}" >&2; cat "$TMPD/err" >&2; exit 1; }
  clang -g "$TMPD/t.ll" runtime/sprout_runtime.c -O0 {{clang_extra}} -o "$TMPD/t.bin" -Wno-override-module 2>"$TMPD/err" \
    || { echo "gc-trace: link failed for {{file}}" >&2; cat "$TMPD/err" >&2; exit 1; }
  lldb -b \
    -o "settings set target.env-vars SPROUT_GC_STRESS=1" \
    -o "command script import scripts/gc_free_trace.py" \
    -o "gctrace {{watch_fn}}" \
    -o "run" -o "quit" "$TMPD/t.bin"

# CPR differential parity check.  Emits each shared-OK corpus file via both
# --use-direct-codegen (direct backend) and --use-ir-codegen (typed), extracts
# external function signatures, compares the INTERSECTION.  Divergence there =
# candidate CPR/ABI parity bug — fail unless allowlisted in
# tests/CPR_DIFF_ALLOWLIST with justification.
#
# Catches what `opt --passes=verify` cannot: structural drift between codegen
# paths where each side is internally consistent but the two disagree.  This
# is the silent-runtime-crash class — exactly what CPR ABI bugs look like.
[group('ir-codegen')]
cpr-differential-check: bootstrap-from-seed
  bash scripts/cpr_differential_check.sh

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
