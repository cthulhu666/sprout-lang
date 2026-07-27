set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

stdlib_root := justfile_directory() / "stdlib"
driver      := stdlib_root / "compiler" / "compile_driver.sprout"
clang_extra := if os() == "macos" { "-framework Security -framework CoreFoundation" } else { "" }
build_dir   := justfile_directory() / "build"
# Single source of truth for the runtime C sources. A glob so splitting
# sprout_runtime.c into more files (scheduler, GC, net, …) needs zero build edits.
# Used UNQUOTED in recipes so bash expands it; every runtime .c is compiled+linked.
runtime_src := "runtime/*.c"

# Graphics backend (raylib) — compiled & linked ONLY by `run-gfx`, never by the
# core build/tests/seed. Override the raylib location with SPROUT_RAYLIB_PREFIX.
raylib_prefix := env_var_or_default("SPROUT_RAYLIB_PREFIX", `brew --prefix raylib 2>/dev/null || echo /opt/homebrew/opt/raylib`)
gfx_src  := justfile_directory() / "graphics" / "sprout_gfx.c"
gfx_link := if os() == "macos" { "-lraylib -framework Cocoa -framework IOKit -framework CoreVideo -framework OpenGL" } else { "-lraylib -lGL -lm -lpthread -ldl -lrt -lX11" }

default:
  @just --list

# Wire the tracked .githooks/ directory as the active hook path (run once after cloning).
[group('bootstrap')]
install-hooks:
  git config core.hooksPath .githooks
  @echo "Hooks installed — .githooks/pre-commit is now active."

# Bypass the bootstrap seed gate for one commit when a compiler change does not
# affect IR output.  Run `just verify-bootstrap-fixed-point` first to confirm.
[group('bootstrap')]
seed-fp-ack:
  #!/usr/bin/env bash
  set -euo pipefail
  cd "{{invocation_directory()}}"
  ACK="$(git rev-parse --git-dir)/seed-fp-ack"
  git write-tree > "$ACK"
  echo "Seed fixed-point acked for staged tree: $(cat "$ACK")"

# Launch the interactive Sprout REPL via sproutd (self-configuring).
# Prerequisites: just build-sproutd
[group('dev')]
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
  clang "$TMP_LL" {{runtime_src}} -O2 {{clang_extra}} -o "$OUT"
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
check file: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  "{{build_dir}}/compile_driver_bin_stage1" --phase check "{{stdlib_root}}" {{quote(file)}}

# Compile {{file}} with stage-1 and run the resulting binary.
[group('dev')]
run file: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_run_$$.ll"
  TMP_BIN="/tmp/sprout_run_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} -O2 {{clang_extra}} -o "$TMP_BIN"
  "$TMP_BIN"

# Build & run a GRAPHICS program: like `run`, but also compiles the raylib shim
# (graphics/sprout_gfx.c) and links raylib + its system frameworks. Requires
# raylib installed (brew install raylib); override its location with
# SPROUT_RAYLIB_PREFIX. Set SPROUT_GFX_MAX_FRAMES=N to auto-close after N frames.
[group('dev')]
run-gfx file *args: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_gfx_$$.ll"
  TMP_BIN="/tmp/sprout_gfx_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} "{{gfx_src}}" -O2 -I"{{raylib_prefix}}/include" -L"{{raylib_prefix}}/lib" {{clang_extra}} {{gfx_link}} -o "$TMP_BIN"
  "$TMP_BIN" {{args}}

# Build {{file}} with GC profiling compiled in (-DSPROUT_GC_PROFILE) and run it
# with SPROUT_GC_PROFILE=1, printing a "[gc profile] ..." summary to stderr at
# exit: cycles, heap_lookup_calls, drain edges, sweep visits, mark-root slots,
# gc_us, trace hits/misses, region_count, and freelist_hits. The hot-path
# counters are compile-time gated, so a normal `just run` build is byte-identical.
[group('dev')]
gc-profile file: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_gcprof_$$.ll"
  TMP_BIN="/tmp/sprout_gcprof_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} -O2 -DSPROUT_GC_PROFILE {{clang_extra}} -o "$TMP_BIN"
  SPROUT_GC_PROFILE=1 "$TMP_BIN"

# Emit LLVM IR for {{file}} to {{out}} using stage-1.
[group('dev')]
compile file out: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > {{quote(out)}}

# Compile {{file}} to a native binary at {{out}} using stage-1.
[group('dev')]
compile-native file out: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_compile_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} -O2 {{clang_extra}} -o {{quote(out)}}

# Compile {{file}} to a debug binary at {{out}} using stage-1 (DWARF, no optimisation).
# Use: just build-debug path/to/prog.spr ./prog_dbg && lldb ./prog_dbg
[group('dev')]
build-debug file out: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_debug_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir --debug "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} -g -O0 {{clang_extra}} -o {{quote(out)}}

# Compile {{file}} with debug info and launch it under lldb.
[group('dev')]
debug-run file: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_debug_$$.ll"
  TMP_BIN="/tmp/sprout_debug_$$"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir --debug "{{stdlib_root}}" {{quote(file)}} > "$TMP_LL"
  clang "$TMP_LL" {{runtime_src}} -g -O0 {{clang_extra}} -o "$TMP_BIN"
  lldb "$TMP_BIN"

# ── Testing ───────────────────────────────────────────────────────────────────

# Run all stdlib + compiler-stage tests (stage-1).
[group('test')]
test: test-stdlib-stage1 test-type-errors test-package-resolution gfx-smoke test-loam

# Second-root (--package-root) module resolution gate: an app importing a module
# from an extra package root resolves only when that root is registered
# (docs/packaging-v0.md §10 phase 2). See scripts/package_resolution_gate.sh.
[group('test')]
test-package-resolution: bootstrap-from-seed
  bash scripts/package_resolution_gate.sh

# Loam game-engine tests. loam.* lives OUTSIDE stdlib_root (it is game code, not
# standard library), so it resolves via --package-root (the repo root). The engine
# is renderer-independent — no gfx — so unlike the gfx examples these tests link
# against the core runtime and actually RUN, asserting the game loop headless.
[group('test')]
test-loam: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{build_dir}}/compile_driver_bin_stage1"
  ROOT="{{justfile_directory()}}"
  TMPD=$(mktemp -d /tmp/sprout_loam_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  fail=0
  for f in tests/loam/*.spr; do
    [ -f "$f" ] || continue
    if ! "$STAGE" --emit-ir "{{stdlib_root}}" --package-root "$ROOT" "$f" > "$TMPD/t.ll" 2>"$TMPD/err"; then
      echo "test-loam: COMPILE FAILED for $f" >&2; cat "$TMPD/err" >&2; fail=1; continue
    fi
    if ! clang "$TMPD/t.ll" {{runtime_src}} {{clang_extra}} -o "$TMPD/t.bin" 2>"$TMPD/err"; then
      echo "test-loam: LINK FAILED for $f" >&2; cat "$TMPD/err" >&2; fail=1; continue
    fi
    if ! "$TMPD/t.bin" > "$TMPD/run" 2>&1 || ! grep -q "SUITE PASSED" "$TMPD/run"; then
      echo "test-loam: $f did not pass" >&2; cat "$TMPD/run" >&2; fail=1; continue
    fi
    echo "  OK $f"
  done
  [ "$fail" -eq 0 ] && echo "==> test-loam ✓" || { echo "==> test-loam FAILED" >&2; exit 1; }

# B1-Double regression gate: assert the inline Vector-Double optimization fires on
# genuine `Vector Double`, does NOT fire on a shadowed heap `Double` (UAF guard),
# still allows partial application, and traps on out-of-bounds. See scripts/b1_gate.sh.
[group('test')]
b1-gate: bootstrap-from-seed
  bash scripts/b1_gate.sh

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
  # Pre-compile the runtime once (each source -> its own .o); tests link the set.
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "ERROR: runtime compile failed ($rtsrc)"; cat "$TMPD/rt.err"; exit 1; }
  done
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
        clang "$TMPD/$idx.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/$idx.bin" 2>"$TMPD/$idx.err"
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
  # Single binary: link the runtime sources directly (no object cache needed).
  echo "==> {{file}}"
  if ! "./$STAGE" --emit-ir "{{stdlib_root}}" "{{file}}" > "$TMP_LL" 2>"$TMP_ERR"; then
    echo "  COMPILE FAILED:"; cat "$TMP_ERR"; exit 1
  fi
  if ! clang "$TMP_LL" {{runtime_src}} {{clang_extra}} -o "$TMP_BIN" 2>"$TMP_ERR"; then
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
  TMP_BIN="{{out_bin}}.$$.tmp"
  trap 'rm -f "$TMP_LL" "$TMP_BIN"' EXIT
  echo "==> Emitting LLVM IR via {{in_bin}}..."
  # --emit-ir exits 0 even on a source error, writing "ERROR: ..." into the output
  # instead of IR (the byte-identity blind spot). Detect that here and fail loudly
  # with the actual source error, rather than letting `opt` fail later with a
  # cryptic "expected top-level entity" that hides the real cause.
  ./{{in_bin}} --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  # Diagnostics are "ERROR: bundle: ..." (parse) or "<line>:<col>: ERROR: check: ..."
  # (typecheck) — both anchored at line start. The prefix guards against matching
  # "ERROR:" inside emitted IR string constants (which begin with @.str).
  if grep -qE "^([0-9]+:[0-9]+: )?ERROR:" "$TMP_LL"; then
    echo "ERROR: compile failed while emitting IR via {{in_bin}} — source error:" >&2
    grep -E "^([0-9]+:[0-9]+: )?ERROR:" "$TMP_LL" | head -8 >&2
    exit 1
  fi
  echo "==> Validating IR..."
  opt --passes=verify "$TMP_LL" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  # Build to a temp then move, so a failed build never leaves a STALE {{out_bin}}
  # behind (which would silently be used by the next step as if freshly built).
  clang "$TMP_LL" {{runtime_src}} -O2 {{clang_extra}} -o "$TMP_BIN"
  mv -f "$TMP_BIN" "{{out_bin}}"
  echo "==> Built {{out_bin}}"

# Build compile_driver_bin_stage2 from stage-1.
[group('build')]
build-stage2: (_build-stage "build/compile_driver_bin_stage1" "build/compile_driver_bin_stage2")

# Build compile_driver_bin_stage3 from stage-2.
[group('build')]
build-stage3: (_build-stage "build/compile_driver_bin_stage2" "build/compile_driver_bin_stage3")

# Build stage-2 with AddressSanitizer + UBSan (slow; for debugging only).
[group('build')]
build-stage2-asan: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_stage2_asan_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR via stage-1..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL"
  echo "==> Linking with clang + ASan/UBSan..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" {{runtime_src}} -O1 -fsanitize=address,undefined {{clang_extra}} -o "{{build_dir}}/compile_driver_bin_stage2_asan"
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
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "ERROR: runtime compile failed ($rtsrc)"; cat "$TMPD/rt.err"; exit 1; }
  done
  declare -a pids=()
  declare -a outs=()
  declare -a stats=()
  idx=0
  active=0
  for f in examples/*.sprout examples/*/*.sprout; do
    [ -f "$f" ] || continue
    outs+=("$TMPD/$idx.out")
    stats+=("$TMPD/$idx.st")
    (
      set +e
      is_xfail=0
      for xf in $XFAIL_EXAMPLES; do [[ "$f" == "$xf" ]] && is_xfail=1 && break; done
      printf '==> %s\n' "$f" > "$TMPD/$idx.out"
      ok=1
      "./$STAGE" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$TMPD/$idx.ll" 2>"$TMPD/$idx.err"
      if [[ $? -ne 0 ]]; then
        { printf '  COMPILE FAILED:\n'; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      elif ! opt --passes=verify "$TMPD/$idx.ll" -o /dev/null 2>"$TMPD/$idx.err"; then
        { printf '  IR INVALID (opt --passes=verify):\n'; cat "$TMPD/$idx.err"; } >> "$TMPD/$idx.out"; ok=0
      else
        clang "$TMPD/$idx.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/$idx.bin" 2>"$TMPD/$idx.err"
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
# Known xfail: the graphics examples (import stdlib.gfx) need the raylib shim +
# link flags from `just run-gfx`, so they cannot link against the core runtime
# here. (This lane now registers the repo as a package root, so examples importing
# examples.* — e.g. sentry_issue_browser — resolve and compile.)
[group('examples')]
compile-examples-stage1: (_compile-examples "build/compile_driver_bin_stage1" "examples/gfx/spinning_cube.sprout examples/gfx/character_view.sprout examples/gfx/character_animated.sprout examples/gfx/character_crowd.sprout examples/gfx/ecs_agents.sprout examples/gfx/ecs_flocking.sprout examples/gfx/terrain_demo.sprout examples/gfx/terrain_rivers_demo.sprout examples/gfx/galaxy_map.sprout")

# Negative type-checking conformance: each tests/conformance/type_error/<n>.spr must
# be rejected by `--phase check` with output containing the substring in <n>.err.
# (`--phase check` exits 0 even on type errors, so matching is by output content.)
# xfail = fixtures whose expected diagnostic is not yet produced (tracked TODO).
[private]
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
  OUT="{{build_dir}}/compile_driver_bin_stage1"
  if [[ ! -f "$SEED" ]]; then
    echo "ERROR: $SEED not found." >&2
    exit 1
  fi
  # No-op guard: if stage-1 binary is already up-to-date with seed + runtime,
  # skip the rebuild. CI steps each invoke `just bootstrap-from-seed` as a
  # `just` dependency in a fresh process, so just's dedupe doesn't apply —
  # without this guard the bootstrap runs 5+ times per CI run.
  rt_stale=0
  for rtsrc in {{runtime_src}}; do [[ "$OUT" -nt "$rtsrc" ]] || rt_stale=1; done
  if [[ -x "$OUT" && "$OUT" -nt "$SEED" && $rt_stale -eq 0 ]]; then
    echo "==> Stage-1 binary is up-to-date with seed + runtime; skipping bootstrap."
    exit 0
  fi
  echo "==> Validating IR seed..."
  opt --passes=verify "$SEED" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$SEED" {{runtime_src}} -O2 {{clang_extra}} -o "$OUT"
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
    clang "$NEXT" {{runtime_src}} -O2 {{clang_extra}} -o "{{build_dir}}/compile_driver_bin_stage1"
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
[group('smoke')]
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

# gfx binding compile-smoke. The stdlib.gfx surface calls into the raylib host
# shim, which links only under `run-gfx` — so these files can't be run in the
# test harness. This gate compiles each to IR (type-checking the extern surface
# and resolving every binding) and asserts the gfx externs reached the IR as
# `declare`s. No link, no run.
[group('smoke')]
gfx-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_gfxsmk_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  failed=0
  for f in tests/gfx_smoke/*.spr; do
    [ -f "$f" ] || continue
    ir="$TMPD/$(basename "$f").ll"
    if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$f" > "$ir" 2>"$TMPD/err"; then
      echo "gfx-smoke: emit-IR failed for $f" >&2; cat "$TMPD/err" >&2
      failed=$((failed + 1)); continue
    fi
    if grep -q '^ERROR' "$ir"; then
      echo "gfx-smoke: $f produced an ERROR line in IR" >&2; grep '^ERROR' "$ir" >&2
      failed=$((failed + 1)); continue
    fi
    if ! grep -q 'declare .*@gfx_draw_fps(' "$ir"; then
      echo "gfx-smoke: $f did not emit a declare for gfx_draw_fps" >&2
      failed=$((failed + 1))
    fi
  done
  if (( failed > 0 )); then
    echo "gfx-smoke: $failed file(s) failed" >&2; exit 1
  fi
  echo "==> gfx-smoke ✓"

# DoD #8 — bundle smoke.  `--phase bundle` on token.sprout, ast.sprout, and
# prelude.sprout must produce non-empty output with no dot-prefix qualified names.
[group('smoke')]
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
[group('smoke')]
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

# Dispatch-trace guard.  SPROUT_TRACE_DISPATCH=1 must emit a `[dispatch] ...` line
# per constrained call site, and a projection sort (`vec_sort_by` with key type !=
# element type) must resolve through the PRECISE branch (`path=precise-just -> Ord
# Int`) — the invariant the df36c0d canonicalize-markers fix established. Also
# asserts the flag is zero-output when unset (gating works). Regression for the
# dict-dispatch soundness diagnostic (BACKLOG "Dispatch Soundness & Diagnostics"
# item 2). See docs/retro-dict-dispatch-soundness-2026-07-13.md.
[group('smoke')]
trace-dispatch-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_tds_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/trace_dispatch/projection_sort.spr
  # With the flag set: compile must succeed and the projection sort must trace the precise path.
  if ! SPROUT_TRACE_DISPATCH=1 "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/err"; then
    echo "trace-dispatch-smoke: fixture failed to compile:" >&2; cat "$TMPD/err" >&2; exit 1
  fi
  if ! grep -q '\[dispatch\].*path=precise-just -> Ord Int' "$TMPD/err"; then
    echo "trace-dispatch-smoke: expected a '[dispatch] ... path=precise-just -> Ord Int' line for the projection sort; got:" >&2
    grep '\[dispatch\]' "$TMPD/err" >&2 || echo "  (no [dispatch] lines at all)" >&2
    exit 1
  fi
  # With the flag unset: zero [dispatch] output (gating / zero-cost-when-off).
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > /dev/null 2>"$TMPD/err_off"
  if grep -q '\[dispatch\]' "$TMPD/err_off"; then
    echo "trace-dispatch-smoke: emitted [dispatch] lines without SPROUT_TRACE_DISPATCH set:" >&2
    cat "$TMPD/err_off" >&2; exit 1
  fi
  echo "==> trace-dispatch-smoke ✓"

# Dict-passing verifier guard.  The dispatch verifier (retro item 1, phase 1) is
# default-fatal: it must ACCEPT legitimate code (no false positive) AND be ACTIVE
# (verified>=1 on a real constrained call — a silent no-op is a regression). The
# projection sort exercises `vec_sort_by ... where Ord k`. Regression for the
# BACKLOG "Dispatch Soundness & Diagnostics" item 1. See docs/debugging.md.
[group('smoke')]
verify-dispatch-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_vds_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/trace_dispatch/projection_sort.spr
  # 1. Default-fatal verifier must accept legit code (no false positive).
  if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/err"; then
    echo "verify-dispatch-smoke: verifier REJECTED a legitimate projection sort (false positive):" >&2
    grep -i verify "$TMPD/err" >&2 || cat "$TMPD/err" >&2
    exit 1
  fi
  # 2. Verifier must be ACTIVE: verified>=1 (else it is a silent no-op).
  stats=$(SPROUT_VERIFY_DISPATCH_STATS=1 "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" 2>&1 >/dev/null | grep 'stats:' | head -1)
  verified=$(printf '%s' "$stats" | sed -n 's/.*verified=\([0-9][0-9]*\).*/\1/p')
  if [ -z "$verified" ] || [ "$verified" -lt 1 ]; then
    echo "verify-dispatch-smoke: verifier did not fire (expected verified>=1); got: '$stats'" >&2
    exit 1
  fi
  echo "==> verify-dispatch-smoke ✓ ($stats)"

# Typed-codegen argv gate.  The typed `main` shim (ir_lowering.main_shim) must
# call @sprout_set_argv(argc, argv) so a typed-built binary's argv_all() sees
# its command-line arguments — the typed-codegen flip self-compiles the
# compiler, whose main() reads argv.  The parity corpus runs every binary with
# NO args, so this is the ONLY gate exercising argv_all() under typed codegen.
[group('smoke')]
argv-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_argv_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/argv_smoke/argv_echo.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/err"; then
    echo "argv-smoke: typed emit failed" >&2; cat "$TMPD/err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "argv-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  got=$("$TMPD/bin" ping hello)
  if [[ "$got" != "pong:hello" ]]; then
    echo "argv-smoke: typed-built binary mishandled argv — expected 'pong:hello', got '$got'" >&2
    echo "  (typed main shim likely missing @sprout_set_argv; see ir_lowering.main_shim)" >&2
    exit 1
  fi
  echo "==> argv-smoke ✓"

# DoD #9 — APPROVED_BUILTINS guard.  Every non-static `long long <name>(` in
# any runtime source (runtime/*.c) must be listed in runtime/APPROVED_BUILTINS.
# Per AGENTS.md "Builtin vs Stdlib" rules 4–6.
[group('smoke')]
check-approved-builtins:
  #!/usr/bin/env bash
  set -euo pipefail
  APPROVED=runtime/APPROVED_BUILTINS
  if [[ ! -f "$APPROVED" ]]; then
    echo "check-approved-builtins: missing $APPROVED" >&2; exit 1
  fi
  # Names declared across ALL runtime sources (excluding `static long long`).
  # grep -h suppresses the file: prefix so the ^long long anchor still matches.
  declared=$(grep -hE '^long long [a-z_][a-zA-Z0-9_]*\(' {{runtime_src}} | sed -E 's/^long long ([a-z_][a-zA-Z0-9_]*)\(.*/\1/' | sort -u)
  # Names listed in APPROVED_BUILTINS (strip comments and whitespace).
  approved=$(sed -E 's/#.*$//; s/^[[:space:]]+|[[:space:]]+$//g' "$APPROVED" | grep -v '^$' | sort -u)
  missing=$(comm -23 <(echo "$declared") <(echo "$approved") || true)
  if [[ -n "$missing" ]]; then
    echo "check-approved-builtins: builtins in runtime/*.c missing from $APPROVED:" >&2
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
[group('smoke')]
run-example-canary: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_canary_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "run-example-canary: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
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
    if ! clang "$ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$bin" 2>"$TMPD/err"; then
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

# Stack-overflow diagnostic regression (CI gate). A deeply (non-tail) recursive
# program overflows the native stack; the runtime must catch it on its alternate
# signal stack and panic cleanly ("stack overflow" + a backtrace) instead of
# dying with a bare, silent SIGSEGV. -rdynamic (Linux only) makes the backtrace
# frames named rather than bare addresses; macOS symbolises from the symbol table.
[group('smoke')]
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
  if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 $RDYN {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
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

# (The former `task-guard-smoke` recipe was retired at L0.3: both guards it tested
# became obsolete — nested scopes are now supported, and under the top-level pump
# task-0 is a materialized task so `task_yield` from main is a legal no-op rather
# than an error. See docs/concurrency-layer0-io-park-design.md.)

# L0.3 I/O-parking smoke (CI gate). Two green tasks share a loopback pair: `reader`
# calls tcp_read before data exists, `writer` then sends. With I/O parking the
# reader suspends on EAGAIN, the scheduler runs the writer, and the read is woken —
# the program COMPLETES printing the interleaved order. With a blocking baseline it
# would DEADLOCK (reader freezes the thread), so the RED signal is a hang, caught by
# a timeout. Also run under SPROUT_GC_STRESS=1: a value held across the park must
# survive a collection driven by the sibling. Needs kqueue (macOS) / epoll (Linux)
# — the epoll backend is only exercised on the Linux CI runner.
[group('smoke')]
task-io-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_taskio_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  build() {  # $1 = fixture -> $TMPD/bin
    if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$1" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
      echo "task-io-smoke: emit-IR failed for $1" >&2; cat "$TMPD/emit.err" >&2; exit 1
    fi
    if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
      echo "task-io-smoke: link failed for $1" >&2; cat "$TMPD/link.err" >&2; exit 1
    fi
  }
  run_once() {  # $1 = label, $2 = required substring; env (e.g. SPROUT_GC_STRESS) from caller
    local label="$1" want="$2"
    set +e
    # perl alarm = portable timeout (macOS lacks `timeout`); a HANG -> non-zero exit.
    perl -e 'alarm 15; exec @ARGV' "$TMPD/bin" > "$TMPD/run.out" 2>"$TMPD/run.err"
    local ec=$?
    set -e
    if [ "$ec" -ne 0 ]; then
      echo "task-io-smoke [$label]: did not complete (exit $ec) — likely a HANG (parking broken)" >&2
      echo "--- stdout ---" >&2; cat "$TMPD/run.out" >&2; echo "--- stderr ---" >&2; cat "$TMPD/run.err" >&2
      exit 1
    fi
    if ! grep -q "$want" "$TMPD/run.out"; then
      echo "task-io-smoke [$label]: missing expected output '$want'" >&2
      cat "$TMPD/run.out" >&2; exit 1
    fi
  }
  # (1) minimal read-park: reader parks, sibling write wakes it (assert interleaved order).
  build tests/task_io_smoke/concurrent_read.spr
  run_once "read-park" "reader got ping"
  if ! awk '/writer sent/{w=NR} /reader got ping/{if(w && NR>w) ok=1} END{exit !ok}' "$TMPD/run.out"; then
    echo "task-io-smoke: wrong order (writer should precede the woken reader)" >&2
    cat "$TMPD/run.out" >&2; exit 1
  fi
  SPROUT_GC_STRESS=1 run_once "read-park/stress" "reader got ping"
  # (2) accept-park + poller RE-ARM (park->wake->park again on one fd) + write side.
  # Reaching "round2" proves the second read-park on the same fd was re-armed.
  build tests/task_io_smoke/echo_roundtrip.spr
  run_once "rearm" "client round2 ack2"
  SPROUT_GC_STRESS=1 run_once "rearm/stress" "client round2 ack2"
  # (2b) http_server concurrency: serve_n spawns a per-connection green task, so a slow
  # first connection must not block a second. client1 (accepted first, via a `ready`
  # channel that orders the connects) sends a PARTIAL request then sleeps; client2 sends
  # a FULL request and must be served FIRST. Concurrent -> [client2, client1]; the old
  # serial handle-inline loop -> [client1, client2]. Both TERMINATE, so a wrong order
  # (not a hang) is the RED, asserted with the awk order idiom.
  build tests/task_io_smoke/http_concurrent_serve.spr
  run_once "http-serve" "client1 served"
  if ! awk '/client2 served/{w=NR} /client1 served/{if(w && NR>w) ok=1} END{exit !ok}' "$TMPD/run.out"; then
    echo "task-io-smoke: http_server serialized (client2 should be served before the slow client1)" >&2
    cat "$TMPD/run.out" >&2; exit 1
  fi
  SPROUT_GC_STRESS=1 run_once "http-serve/stress" "client1 served"
  # (2c) http_server unbounded serve: `serve` (no connection count) keeps accepting past
  # any small bound. One client opens THREE sequential connections; all must be answered
  # (asserted on "req3 ok"). A bounded mis-impl (serve_n(port,1)) closes the listener after
  # the first, so req2/req3 fail. serve never returns, so the owner force-drops it with
  # scope_cancel once the client signals done; reaching "done" proves the drop released the
  # join (a broken drop HANGS -> alarm fires).
  build tests/task_io_smoke/http_serve_forever.spr
  run_once "http-serve-forever" "req3 ok"
  if ! grep -q "done" "$TMPD/run.out"; then
    echo "task-io-smoke: unbounded serve did not shut down (scope_cancel drop of parked serve failed)" >&2
    cat "$TMPD/run.out" >&2; exit 1
  fi
  SPROUT_GC_STRESS=1 run_once "http-serve-forever/stress" "req3 ok"
  # (2d) http_server connection-error isolation (C1): a per-connection handler whose socket write
  # FAILS (the client closed early, so the ~512 KiB response resets the peer) must drop only that
  # connection, not exit(1) the whole process. The server uses the recoverable tcp_read_avail/
  # tcp_write_string, so the crashing connection's write returns an Err the handler swallows; the
  # good client is still served and the scope joins. Reaching "done" proves the process survived —
  # the fatal tcp_read/tcp_write it replaced aborted here (RED = non-zero exit, no "done").
  build tests/task_io_smoke/http_conn_error_survives.spr
  run_once "http-conn-error" "done"
  SPROUT_GC_STRESS=1 run_once "http-conn-error/stress" "done"
  # (2e) tcp_read_avail error path: reading from an unallocated handle returns
  # Err(TcpInvalidHandle) instead of exit(1) (the recoverable counterpart to fatal tcp_read).
  # Deterministic — no socket timing. Reaching "done" proves the Err branch.
  build tests/task_io_smoke/tcp_read_avail_bad_handle.spr
  run_once "tcp-read-avail-bad-handle" "done"
  # (3) I/O-drop cancellation (L0.5): scope_cancel force-drops tasks parked in the
  # poller (both task_fork and task_spawn) so __scope_join returns instead of blocking
  # the pump forever. Reaching "done" proves the drop; a broken drop HANGS (alarm fires).
  build tests/task_io_smoke/cancel_io_drop.spr
  run_once "cancel-io-drop" "done"
  SPROUT_GC_STRESS=1 run_once "cancel-io-drop/stress" "done"
  # (4) await-a-dropped-task guard (L0.5): awaiting a task that scope_cancel dropped must
  # LOUD-FAIL, not hang. run_expect_fail asserts a non-zero exit AND the guard message
  # (a HANG would trip the alarm -> also non-zero, so we additionally require the message).
  run_expect_fail() {  # $1 = label, $2 = required stderr substring
    local label="$1" want="$2"
    set +e
    perl -e 'alarm 15; exec @ARGV' "$TMPD/bin" > "$TMPD/run.out" 2>"$TMPD/run.err"
    local ec=$?
    set -e
    if [ "$ec" -eq 0 ]; then
      echo "task-io-smoke [$label]: expected a loud-fail abort, but exited 0" >&2
      cat "$TMPD/run.out" >&2; exit 1
    fi
    if ! grep -q "$want" "$TMPD/run.err"; then
      echo "task-io-smoke [$label]: exited $ec but stderr lacked '$want' (a HANG, not the guard?)" >&2
      echo "--- stderr ---" >&2; cat "$TMPD/run.err" >&2; exit 1
    fi
  }
  build tests/task_io_smoke/await_dropped_fails.spr
  run_expect_fail "await-dropped" "dropped by scope_cancel"
  # (5) task_sleep fired-timer drop (L0.6): a sleeper whose timer fired kernel-side while
  # the owner ran must be safely force-dropped (poll_remove_timer discards the stale event)
  # so a later poll_wait cannot resume a freed task. Reaching "done" proves it; a leaked
  # stale token corrupts (verified by a no-op-remove_timer ASan negative control).
  build tests/task_io_smoke/cancel_timer_drop.spr
  run_once "timer-drop" "done"
  SPROUT_GC_STRESS=1 run_once "timer-drop/stress" "done"
  # (6) with_timeout I/O-drop (L0.7): a body parked on tcp_accept must be FORCE-DROPPED when its
  # deadline fires (poll_remove(fd) + reclaim roots+stack), so __scope_join returns instead of
  # blocking the pump forever. Reaching "done" proves the fd-park drop; a broken drop HANGS.
  build tests/task_io_smoke/timeout_io_drop.spr
  run_once "timeout-io-drop" "done"
  SPROUT_GC_STRESS=1 run_once "timeout-io-drop/stress" "done"
  # (7) with_timeout MVP boundary (L0.7): timing out a body blocked in a NESTED with_scope join
  # must LOUD-FAIL (the tree-cancel cascade is deferred), not hang or orphan the inner scope.
  build tests/task_io_smoke/timeout_nested_loudfail.spr
  run_expect_fail "timeout-nested-loudfail" "nested scope/await"
  # (8) channel-drop cancellation (L0.8): scope_cancel force-drops tasks parked in chan_recv on
  # an empty channel (both task_fork and task_spawn), so __scope_join returns instead of the pump
  # deadlock-panicking. Reaching "done" proves the drop; a broken drop panics/hangs (non-zero).
  build tests/task_io_smoke/cancel_chan_drop.spr
  run_once "cancel-chan-drop" "done"
  SPROUT_GC_STRESS=1 run_once "cancel-chan-drop/stress" "done"
  # (9) with_timeout over a channel recv (L0.8): a body parked in chan_recv must be force-dropped
  # when the deadline fires (__await_deadline PARK_CHAN classification), so with_timeout returns
  # Expired and the scope joins. Reaching "done" proves it; a broken drop panics/hangs.
  build tests/task_io_smoke/timeout_chan_drop.spr
  run_once "timeout-chan-drop" "done"
  SPROUT_GC_STRESS=1 run_once "timeout-chan-drop/stress" "done"
  # (10) channel capacity guard (L0.10): chan_new with a NEGATIVE capacity must LOUD-FAIL. Cap 0 is
  # now valid (rendezvous / unbuffered); only a nonsensical (< 0) capacity is rejected.
  build tests/task_io_smoke/chan_negative_cap_fails.spr
  run_expect_fail "chan-negative-cap" "capacity must be >= 0"
  # (10b) rendezvous send-park drop (L0.10): scope_cancel force-drops a task parked in chan_send on
  # a cap-0 channel nobody receives from (both fork + spawn). The buffered fixtures only drop RECV-
  # parked tasks, so this covers the send_waiters force-drop path. "done" proves it; broken = panic.
  build tests/task_io_smoke/cancel_rendezvous_send_drop.spr
  run_once "rendezvous-send-drop" "done"
  SPROUT_GC_STRESS=1 run_once "rendezvous-send-drop/stress" "done"
  # (11) channel close guard (L0.9): sending into a CLOSED channel must LOUD-FAIL (send-after-close
  # is a program bug; Sprout has no recovery, so it aborts rather than dropping the value silently).
  build tests/task_io_smoke/send_on_closed_fails.spr
  run_expect_fail "send-on-closed" "send on closed channel"
  # (12) double-close guard (L0.9): closing an ALREADY-CLOSED channel must LOUD-FAIL (Go panics on
  # double-close — a synchronization bug), not silently no-op.
  build tests/task_io_smoke/double_close_fails.spr
  run_expect_fail "double-close" "channel already closed"
  # (13) send-PARKED-then-close guard (L0.9): a sender parked on a FULL channel that is then closed
  # must abort on resume (the post-park check, distinct from #11's send-entry check), not return.
  build tests/task_io_smoke/send_parked_close_fails.spr
  run_expect_fail "send-parked-close" "send on closed channel"
  # (16) select cancel-drop (L0.11): scope_cancel force-drops a task parked in chan_select, unlinking
  # it from EVERY channel it listed (both fork + spawn, each on two channels). "done" proves the
  # multi-channel unlink; a broken single-channel unlink double-frees (ASan-verified negative control).
  build tests/task_io_smoke/cancel_select_drop.spr
  run_once "select-cancel-drop" "done"
  SPROUT_GC_STRESS=1 run_once "select-cancel-drop/stress" "done"
  # (17) with_timeout over chan_select (L0.11): a select-parked body is force-dropped when its deadline
  # fires (__await_deadline PARK_SELECT classification), so with_timeout returns Expired and joins.
  build tests/task_io_smoke/timeout_select_drop.spr
  run_once "select-timeout-drop" "done"
  SPROUT_GC_STRESS=1 run_once "select-timeout-drop/stress" "done"
  echo "==> task-io-smoke ✓ (read-park, accept-park, re-arm, http-serve-concurrency, http-conn-error-isolation, tcp-read-avail-error, write, cancel-drop, await-guard, timer-drop, timeout-drop, timeout-nested-guard, chan-cancel-drop, chan-timeout-drop, chan-negative-cap-guard, rendezvous-send-drop, send-on-closed-guard, double-close-guard, send-parked-close-guard, select-cancel-drop, select-timeout-drop; interleaved; stress-clean)"

# Division-by-zero guard regression (CI gate). The fixture divides by a RUNTIME
# zero (`10 / list_length(argv)` with no args), which neither the compiler nor
# clang can fold. A bare `sdiv i64 _, 0` is LLVM undefined behavior; the emitted
# ast_to_ir guard must panic cleanly ("division by zero", non-zero exit) rather
# than return UB garbage.
[group('smoke')]
div-by-zero-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_divz_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/div_smoke/div_by_zero.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "div-by-zero-smoke: emit-IR failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
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

# TCO runtime regression (CI gate): a deep tail-recursive program must run to
# completion under typed codegen (--use-ir-codegen, now the default). The fixture
# carries a heap param rooted across the recursive call, so a non-TCO'd typed
# build either exhausts the GC root pool or overflows the stack (the failure
# WITHOUT a per-iteration root reset on the back-edge). Guards against a TCO
# regression in the self-tail-recursion lowering.
[group('smoke')]
tco-runtime-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_tcort_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/stack_overflow_smoke/deep_tail_recursion.spr
  if ! "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "tco-runtime-smoke: typed emit failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
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

# GC-stress pass (P11-2e lessons): run a curated set of rooting-exercising
# typed-codegen tests under SPROUT_GC_STRESS=1 (collect on EVERY allocation).
# The default-threshold suite hides use-after-free rooting bugs as false greens;
# stress collapses the timing window and fails loudly.  This is the durable
# guard for the whole typed-codegen rooting class.  See project_gc_stress_oracle.
# Grow STRESS_FILES as typed-codegen coverage warrants.
[group('test')]
test-stress: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_stress_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "test-stress: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
  # Gated (must pass under stress).  ctors/match/closures promoted here once the
  # PR 11 item 4 GC-UAF was fixed (ir_rooting: IRCall now roots its heap operands
  # across the call; @ref_new and other builtins may collect before consuming an
  # operand).  All three were the same class — they presented differently (tag-
  # read abort vs EXC_BAD_ACCESS) only by how the swept address was reused.
  # test_task_cooperative: L0.1 green tasks — the canonical multi-task rooting
  # exercise. Under stress a second task allocates while the first is suspended
  # at a yield point; per-task root contexts (sprout_scheduler.c) must keep the
  # suspended task's values live. A rooting regression there presents as a
  # collected-while-live abort here.
  # test_task_nested_scope: nested scopes — the join loop's caller_roots save/
  # restore is the new rooting surface; a leaf allocating while an outer task is
  # suspended inside its nested join must keep the outer task's values live.
  # test_chan / test_chan_close: channel buffer slots, a parked task's chan_pending,
  # and (L0.9) the Got-payload boxed by chan_recv must stay rooted while another task
  # drives a GC — a rooting regression presents as a collected-while-live abort here.
  # test_chan_rendezvous: (L0.10) the direct sender→receiver hand-off carries a heap value
  # through the parked sender's chan_pending across a GC storm — same rooting oracle, cap-0 path.
  # test_chan_select: (L0.11) a parked selector receives a heap value via the send-side select
  # delivery into chan_pending under a GC storm — the select rooting oracle.
  STRESS_FILES="tests/stdlib/test_ir_rooting.spr tests/stdlib/test_ir_codegen_ctors.spr tests/stdlib/test_ir_codegen_match.spr tests/stdlib/test_ir_codegen_closures.spr tests/stdlib/test_ir_codegen_char_rooting.spr tests/stdlib/test_stress_global_roots.spr tests/stdlib/test_stress_unboxed_maybe_heap_payload.spr tests/stdlib/test_stress_cpr_tier2_worker.spr tests/stdlib/test_stress_records_heap.spr tests/stdlib/test_task_cooperative.spr tests/stdlib/test_task_nested_scope.spr tests/stdlib/test_chan.spr tests/stdlib/test_chan_close.spr tests/stdlib/test_chan_rendezvous.spr tests/stdlib/test_chan_select.spr"
  # Known-failing under stress — false-green at the default threshold, FOUND BY
  # THIS PASS (residual typed-codegen rooting UAF, GC-confirmed via
  # SPROUT_GC_DISABLE).  Tracked in BACKLOG.md; warn-only here.  Promote to
  # STRESS_FILES as each is fixed (an UNEXPECTED PASS flags that it's ready).
  STRESS_XFAIL=""
  failed=0
  NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
  JOBS=$(( NCPU > 8 ? 8 : NCPU ))
  run_one() {  # prints "ok" or "fail"; never exits.  Per-file err file avoids the
               # shared-$TMPD/err race when invoked concurrently.
    local f="$1" name ll bin out err
    name=$(basename "$f" .spr); ll="$TMPD/$name.ll"; bin="$TMPD/$name.bin"; err="$TMPD/$name.err"
    "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "$f" > "$ll" 2>"$err" || { echo fail; return; }
    clang "$ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$bin" 2>"$err" || { echo fail; return; }
    if out=$(SPROUT_GC_STRESS=1 "$bin" 2>&1); then
      echo "$out" | grep -q "SUITE FAILED" && echo fail || echo ok
    else
      echo fail
    fi
  }
  # Dispatch every file JOBS-wide; each writes its ok/fail verdict to <name>.result.
  # SPROUT_GC_STRESS (collect-on-every-alloc) makes each run slow and single-
  # threaded, so fanning the fixed file set across the cores is a near-linear win.
  declare -a pids=()
  idx=0; active=0
  for f in $STRESS_FILES $STRESS_XFAIL; do
    [ -f "$f" ] || continue
    name=$(basename "$f" .spr)
    ( run_one "$f" > "$TMPD/$name.result" 2>/dev/null ) &
    pids+=($!); idx=$((idx + 1)); active=$((active + 1))
    if (( active >= JOBS )); then
      wait -n 2>/dev/null || wait "${pids[idx - active]}" || true
      active=$((active - 1))
    fi
  done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  # Tally gated files (must pass): absent result or non-"ok" verdict is a failure.
  for f in $STRESS_FILES; do
    name=$(basename "$f" .spr)
    if [ ! -f "$f" ]; then echo "test-stress: missing $f" >&2; failed=$((failed + 1)); continue; fi
    if [[ "$(cat "$TMPD/$name.result" 2>/dev/null)" == ok ]]; then
      echo "  PASS (stress): $f"
    else
      echo "test-stress: $f FAILED under SPROUT_GC_STRESS=1" >&2; failed=$((failed + 1))
    fi
  done
  # Tally xfail files (tracked; an unexpected pass is informational, never fatal).
  for f in $STRESS_XFAIL; do
    [ -f "$f" ] || continue
    name=$(basename "$f" .spr)
    if [[ "$(cat "$TMPD/$name.result" 2>/dev/null)" == ok ]]; then
      echo "  UNEXPECTED PASS (stress) — promote to STRESS_FILES: $f"
    else
      echo "  xfail (stress, tracked): $f"
    fi
  done
  if (( failed > 0 )); then
    echo "test-stress: $failed gated file(s) failed under SPROUT_GC_STRESS=1" >&2; exit 1
  fi
  echo "==> test-stress ✓"

# Run the independent, single-threaded CI gates concurrently (JOBS-wide) instead
# of as a sequential chain of `just` steps.  On the 4-vCPU CI worker each of these
# gates used only 1 core, leaving 3 idle for the duration; fanning them out fills
# the cores.  Failure propagation is EXPLICIT — each gate's exit status is captured
# and this recipe exits non-zero if ANY gate failed.  (A bare `a & b & wait` would
# report success even when a background gate failed, silently disabling the gate.)
# stage-1 + fmt_bin are built once as deps before fan-out; each gate's own
# `bootstrap-from-seed`/`build-fmt-from-seed` dep then no-ops via its freshness guard.
[group('test')]
ci-fast-gates: bootstrap-from-seed build-fmt-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  JUST="{{just_executable()}}"
  TMPD=$(mktemp -d /tmp/sprout_gates_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
  JOBS=$(( NCPU > 8 ? 8 : NCPU ))
  # "<label>|<gate-command>"; labels are filesystem-safe (result/output filenames).
  GATES=(
    "approved-builtins|check-approved-builtins"
    "smoke-shapes|smoke-shapes"
    "bundle-smoke|bundle-smoke"
    "fmt-check|fmt-check"
    "type-errors|test-type-errors"
    "example-canary|run-example-canary"
    "gc-safety|gc-safety-check --strict"
    "argv-smoke|argv-smoke"
    "div-by-zero-smoke|div-by-zero-smoke"
    "stack-overflow-smoke|stack-overflow-smoke"
    "task-io-smoke|task-io-smoke"
    "tco-runtime-smoke|tco-runtime-smoke"
    "trace-dispatch-smoke|trace-dispatch-smoke"
    "verify-dispatch-smoke|verify-dispatch-smoke"
  )
  declare -a pids=() labels=()
  idx=0; active=0
  for entry in "${GATES[@]}"; do
    label="${entry%%|*}"; cmd="${entry#*|}"
    labels+=("$label")
    # word-split $cmd deliberately: it is a controlled "recipe [args]" string.
    ( $JUST $cmd > "$TMPD/$label.out" 2>&1; echo $? > "$TMPD/$label.status" ) &
    pids+=($!); idx=$((idx + 1)); active=$((active + 1))
    if (( active >= JOBS )); then
      wait -n 2>/dev/null || wait "${pids[idx - active]}" || true
      active=$((active - 1))
    fi
  done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  failed=0
  echo ""
  for label in "${labels[@]}"; do
    st=$(cat "$TMPD/$label.status" 2>/dev/null || echo 1)
    if [ "$st" = 0 ]; then echo "  ✓ $label"; else echo "  ✗ $label (exit $st)"; failed=$((failed + 1)); fi
  done
  if (( failed > 0 )); then
    echo "" >&2
    echo "==> $failed gate(s) FAILED — full output of each below:" >&2
    for label in "${labels[@]}"; do
      st=$(cat "$TMPD/$label.status" 2>/dev/null || echo 1)
      [ "$st" = 0 ] && continue
      echo "" >&2; echo "───── $label (exit $st) ─────" >&2; cat "$TMPD/$label.out" >&2
    done
    exit 1
  fi
  echo "==> all ${#labels[@]} fast gates ✓"

# GC use-after-free free-tracer (P11-2e diagnostic).  Compiles <file> via typed
# codegen with debug info, runs it under lldb + SPROUT_GC_STRESS=1, and stops the
# instant <watch_fn> is entered with a pointer arg (x0) that was already freed,
# printing the victim's full alloc/free lineage.  <watch_fn> MUST be a function
# that RECEIVES the suspected victim as its first argument — find it from the
# crash's abort backtrace first (e.g. the match-dispatch fn that reads the
# corrupted scrutinee), NOT the arg-less sprout_abort_match.  See scripts/gc_free_trace.py.
[group('dev')]
gc-trace file watch_fn: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_gctrace_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" "{{file}}" > "$TMPD/t.ll" 2>"$TMPD/err" \
    || { echo "gc-trace: typed emit failed for {{file}}" >&2; cat "$TMPD/err" >&2; exit 1; }
  clang -g "$TMPD/t.ll" {{runtime_src}} -O0 {{clang_extra}} -o "$TMPD/t.bin" -Wno-override-module 2>"$TMPD/err" \
    || { echo "gc-trace: link failed for {{file}}" >&2; cat "$TMPD/err" >&2; exit 1; }
  lldb -b \
    -o "settings set target.env-vars SPROUT_GC_STRESS=1" \
    -o "command script import scripts/gc_free_trace.py" \
    -o "gctrace {{watch_fn}}" \
    -o "run" -o "quit" "$TMPD/t.bin"

# ── REPL ──────────────────────────────────────────────────────────────────────

# Build sproutd — combined REPL + analysis service binary (self-configuring).
[group('build')]
build-sproutd: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMP_LL="/tmp/sprout_sproutd_$$.ll"
  trap 'rm -f "$TMP_LL"' EXIT
  echo "==> Emitting LLVM IR for sproutd..."
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "{{stdlib_root}}/compiler/sproutd_driver.sprout" > "$TMP_LL"
  echo "==> Validating IR..."
  opt --passes=verify "$TMP_LL" -o /dev/null
  echo "==> Linking with clang..."
  mkdir -p "{{build_dir}}"
  clang "$TMP_LL" {{runtime_src}} -O2 {{clang_extra}} -o "{{build_dir}}/sproutd"
  echo "==> Built {{build_dir}}/sproutd"

# The standalone analysis-service binary is retired: sproutd subsumes it.
# `sproutd --analysis-service <stdlib_root>` runs the identical
# analysis_service_driver.run_service entry (see stdlib/compiler/sproutd_driver.sprout).

# ── Aggregate Gates ───────────────────────────────────────────────────────────
#
# One-shot verification batteries so the pre-commit ritual is a single command
# instead of a hand-assembled `&&` chain.  Both are VERIFICATION-ONLY: they run
# `fmt-check` (not `fmt`), so a failure means "run `just fmt` and re-stage", never
# a silent reformat.
#
#   just gate-quick   fast edit->commit loop  (fmt-check, test, examples, 2 smokes)
#   just gate         full CI parity          (mirrors .forgejo/workflows/ci.yml)
#   just gate-audit   guard: fails if CI runs a `just` task `gate` does not cover
#
# `gate` is a superset of `gate-quick`; a green `gate` means CI will not surprise
# you.  It is slow (~15-25 min: test-stress + task-io-smoke dominate) — use
# gate-quick during iteration and gate before pushing.

# Fast pre-commit battery: the tasks run together most often during iteration.
[group('gate')]
gate-quick: fmt-check test compile-examples-stage1 smoke-shapes bundle-smoke
  @echo "==> gate-quick ✓ (fmt-check · test · compile-examples-stage1 · smoke-shapes · bundle-smoke)"

# Full CI-parity battery.  Dependencies are ordered cheap->expensive so a failure
# surfaces fast; `just` runs them sequentially and deduplicates the shared
# bootstrap-from-seed.  gc-safety-check needs `--strict` to gate (bare it is
# advisory), so it runs in the body rather than as an arg-less dependency.
# Full CI-parity battery (slow, ~15-25m); a green run means CI will not surprise you.
[group('gate')]
gate: fmt-check smoke-shapes bundle-smoke loud-fail-smoke argv-smoke trace-dispatch-smoke verify-dispatch-smoke div-by-zero-smoke stack-overflow-smoke tco-runtime-smoke check-approved-builtins verify-bootstrap-fixed-point compile-examples-stage1 run-example-canary test task-io-smoke test-stress
  #!/usr/bin/env bash
  set -euo pipefail
  echo "==> gate: gc-safety-check --strict..."
  just gc-safety-check --strict
  echo "==> gate ✓ — full CI-parity battery passed; CI will not surprise you."

# Drift guard: assert every `just` task CI runs is covered by `gate`.  Computes
# gate's coverage LIVE by recursively expanding its dependencies via `just --show`
# (so `test` gaining a child needs no edit here), then diffs against the tasks
# grepped out of the CI workflow.  Run this after touching ci.yml or the gate list.
# Assert `just gate` covers every task CI runs (drift guard).
[group('gate')]
gate-audit:
  #!/usr/bin/env bash
  set -euo pipefail
  CI_WORKFLOW=".forgejo/workflows/ci.yml"
  # CI tasks gate intentionally omits: bootstrap/build deps (auto-run) and the
  # mutate-then-check seed path (gate covers it via verify-bootstrap-fixed-point).
  EXCLUDE="bootstrap-from-seed build-fmt-from-seed refresh-seed"
  # Tasks gate runs from its BODY (not reachable via --show dependency expansion).
  BODY="gc-safety-check"
  expand() {  # print a recipe name and, recursively, its dependency recipe names
    local r="$1" line deps d
    echo "$r"
    line=$(just --show "$r" 2>/dev/null | grep -E "^$r *:" | head -1) || return 0
    deps=${line#*:}; deps=${deps%%#*}
    for d in $deps; do [[ "$d" =~ ^[a-z][a-z0-9-]*$ ]] && expand "$d"; done
  }
  gate_set=$(printf '%s\n%s\n' "$(expand gate)" "$BODY" | sort -u)
  ci_tasks=$(grep -oE 'just +[a-z][a-z0-9-]*' "$CI_WORKFLOW" | awk '{print $2}' | sort -u)
  missing=""
  for t in $ci_tasks; do
    grep -qw "$t" <<<"$EXCLUDE" && continue
    grep -qx "$t" <<<"$gate_set" || missing="$missing $t"
  done
  if [[ -n "$missing" ]]; then
    echo "gate-audit ✗ — CI runs these tasks that 'just gate' does not cover:" >&2
    printf '   %s\n' $missing >&2
    echo "   Add each to the 'gate' recipe, or to EXCLUDE if it is intentionally CI-only." >&2
    exit 1
  fi
  echo "==> gate-audit ✓ — gate covers every CI gate task."

# Refresh the bootstrap seed from a GUARANTEED-clean stage-1, then verify the
# fixed point.  Use after any compiler-source edit under stdlib/compiler/.
#
# Why a dedicated recipe: `just refresh-seed` alone can silently reuse a stale
# stage-1 binary — bootstrap-from-seed's freshness guard skips the rebuild when
# the committed seed is unchanged, which is exactly the case mid-edit.  Deleting
# the binary first forces a clean rebuild from the committed seed before the
# fixed-point iteration.  This recipe takes NO dependencies on purpose: recipe
# deps run before the body, so a `bootstrap-from-seed` dep (pulled in transitively
# by refresh-seed) would rebuild stage-1 BEFORE the rm, defeating the guard.
# Refresh the bootstrap seed from a clean stage-1, then verify the fixed point.
[group('bootstrap')]
refresh-seed-clean:
  #!/usr/bin/env bash
  set -euo pipefail
  echo "==> Removing stage-1 binary to force a clean bootstrap..."
  rm -f "{{build_dir}}/compile_driver_bin_stage1"
  echo "==> Refreshing seed (iterates to the new fixed point)..."
  just refresh-seed
  echo "==> Verifying the refreshed seed is a fixed point..."
  just verify-bootstrap-fixed-point
  echo "==> refresh-seed-clean ✓ — stage bootstrap/compile_driver.ll, then commit."
