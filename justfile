set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

stdlib_root := justfile_directory() / "stdlib"
driver      := stdlib_root / "compiler" / "compile_driver.sprout"
clang_extra := if os() == "macos" { "-framework Security -framework CoreFoundation" } else { "" }
build_dir   := justfile_directory() / "build"
# Single source of truth for the runtime C sources. A glob so splitting
# sprout_runtime.c into more files (scheduler, GC, net, …) needs zero build edits.
# Used UNQUOTED in recipes so bash expands it; every runtime .c is compiled+linked.
runtime_src := "runtime/*.c"
# Container-backed Linux gate — see the "Linux gate" section near `task-io-smoke`.
linux_image := "sprout-linux-smoke:ubuntu-24.04"
# Host-side cache for the container's `just` binary. Outside build_dir on purpose: it
# is a tool, not a build artifact, and must survive `just clean`.
linux_cache := justfile_directory() / ".cache" / "linux-smoke"
# MUST track the `just` pin in mise.toml. The checksums below are version-specific, so
# bumping this means bumping both of them (from casey/just's release SHA256SUMS).
linux_just_version := "1.39.0"
linux_just_sha_aarch64 := "f1b9acdb4374983539c765d60374350932527df807b25975e05abb152c9021e7"
linux_just_sha_x86_64  := "1c53fa85a8c021ce7b19814e1a5e1dc0aa10c04bddca75196f7ab6db6130d2cd"

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
test: test-stdlib-stage1 test-type-errors test-parse-errors test-executable-errors test-conformance-run test-package-resolution

# Second-root (--package-root) module resolution gate: an app importing a module
# from an extra package root resolves only when that root is registered
# (docs/packaging-v0.md §10 phase 2). See scripts/package_resolution_gate.sh.
[group('test')]
test-package-resolution: bootstrap-from-seed
  bash scripts/package_resolution_gate.sh

# Golden-stdout conformance gate. Each tests/conformance/run/<name>.spr is
# compiled (stage-1), linked, and run; its stdout must equal <name>.out byte for
# byte. tests/conformance/run/XFAIL quarantines known-broken fixtures WITHOUT
# silently skipping them: a quarantined fixture that starts passing again turns
# the gate RED (so quarantine self-heals), as does an orphan .out with no .spr.
# NOTE: --emit-ir now reports source errors on stderr and exits NONZERO, but this
# gate still matches on OUTPUT CONTENT rather than exit status, and deliberately
# greps BOTH streams. Reason: the fixtures distinguish "compile failed" from "link
# failed" from "wrong output" in the `why` label, and content is what names which.
# Grepping both streams also means the gate does not silently change meaning if a
# diagnostic moves between streams again.
[group('test')]
test-conformance-run: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  STAGE="{{build_dir}}/compile_driver_bin_stage1"
  DIR="tests/conformance/run"
  TMPD=$(mktemp -d /tmp/sprout_conf_run_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  RTO="$TMPD/rtobj"; mkdir -p "$RTO"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$RTO/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "ERROR: runtime compile failed ($rtsrc)"; cat "$TMPD/rt.err"; exit 1; }
  done
  # Load the XFAIL manifest (first token per non-comment line).
  declare -A xfail=()
  if [[ -f "$DIR/XFAIL" ]]; then
    while read -r name _; do
      [[ -z "$name" || "$name" == \#* ]] && continue
      xfail["$name"]=1
    done < "$DIR/XFAIL"
  fi
  failed=0; xfailed=0; passed=0
  # Orphan .out (golden with no source) — the exact rot that hid a deleted fixture.
  for out in "$DIR"/*.out; do
    [ -f "$out" ] || continue
    name="$(basename "$out" .out)"
    [[ -f "$DIR/$name.spr" ]] || { echo "  ORPHAN  $name.out has no $name.spr"; failed=$((failed + 1)); }
  done
  for spr in "$DIR"/*.spr; do
    [ -f "$spr" ] || continue
    name="$(basename "$spr" .spr)"
    golden="$DIR/$name.out"
    ok=1; why=""
    if [[ ! -f "$golden" ]]; then ok=0; why="no .out golden"; fi
    if [[ $ok -eq 1 ]]; then
      "$STAGE" --emit-ir "{{stdlib_root}}" "$spr" > "$TMPD/t.ll" 2>"$TMPD/t.err"
      if grep -qE "^([0-9]+:[0-9]+: )?ERROR:" "$TMPD/t.ll" "$TMPD/t.err"; then
        ok=0; why="compile: $(grep -hE '^([0-9]+:[0-9]+: )?ERROR:' "$TMPD/t.ll" "$TMPD/t.err" | head -1)"
      elif ! clang "$TMPD/t.ll" "$RTO"/*.o {{clang_extra}} -o "$TMPD/t.bin" 2>"$TMPD/t.err"; then
        ok=0; why="link: $(grep -iE 'undefined|error' "$TMPD/t.err" | head -1)"
      else
        # Golden-stdout: assert stdout only, deliberately ignoring the binary's
        # exit code (a fixture that prints the right thing then exits nonzero is
        # not what this corpus checks — see conformance/{run,runtime_error}).
        got="$("$TMPD/t.bin" 2>/dev/null)"
        [[ "$got" == "$(cat "$golden")" ]] || { ok=0; why="stdout mismatch"; }
      fi
    fi
    if [[ -n "${xfail[$name]:-}" ]]; then
      if [[ $ok -eq 1 ]]; then echo "  UNEXPECTED PASS  $name (remove from XFAIL)"; failed=$((failed + 1))
      else echo "  xfail  $name ($why)"; xfailed=$((xfailed + 1)); fi
    else
      if [[ $ok -eq 1 ]]; then passed=$((passed + 1))
      else echo "  FAIL  $name — $why"; failed=$((failed + 1)); fi
    fi
  done
  echo ""
  echo "==> conformance/run: $passed passed, $xfailed xfail (quarantined), $failed failed"
  [ "$failed" -eq 0 ] || { echo "==> test-conformance-run FAILED" >&2; exit 1; }
  echo "==> test-conformance-run ✓"

# B1-Double regression gate: assert the inline Vector-Double optimization fires on
# genuine `Vector Double`, does NOT fire on a shadowed heap `Double` (UAF guard),
# still allows partial application, and traps on out-of-bounds. See scripts/b1_gate.sh.
[group('test')]
b1-gate: bootstrap-from-seed
  SPROUT_STAGE1="{{build_dir}}/compile_driver_bin_stage1" bash scripts/b1_gate.sh

# Byte-diff --use-ir-codegen output for the whole example + smoke-shape corpus
# against the committed goldens in tests/golden/ir/ (57 files, ~55s serial).
#
# This is a CHANGE DETECTOR, not a correctness oracle: it answers "did this edit
# alter the IR of any real program, and is that what you intended?".  That makes
# the golden diff review signal as much as a gate — the diff shows precisely what
# a codegen change did to shipping code.
#
# It also fails on MISSING GOLDEN, so an example that newly becomes IR-compilable
# must be snapshotted rather than silently sitting outside the corpus.
#
# When a diff is INTENTIONAL: run `just ir-golden-snapshot` and stage the result.
# Read the diff first — regenerating without reading it is how a real regression
# gets laundered into an "expected" snapshot.
#
# Wired into `gate` and `ci-fast-gates` deliberately, NOT into `gate-quick`: at
# ~55s it is the single slowest fast-gate, and gate-quick exists for the seconds-
# scale edit loop.  The tradeoff is that a stale golden survives a green
# gate-quick and is caught by CI instead — which is the arrangement that let two
# stale snapshots reach master before this recipe existed, so the mitigation is
# that CI now blocks it, not that the local quick loop catches it.
[group('test')]
ir-golden-diff: bootstrap-from-seed
  bash scripts/ir_golden_diff.sh

# Regenerate tests/golden/ir/ from the CURRENT compiler.  Run only after reading
# the `just ir-golden-diff` output and confirming every change is intended.
[group('dev')]
ir-golden-snapshot: bootstrap-from-seed
  bash scripts/ir_golden_snapshot.sh

[private]
_test-stdlib stage dirs="tests/stdlib tests/stdlib/compiler":
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  JOBS=$(bash scripts/test_jobs.sh)
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
  for dir in {{dirs}}; do
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
      "./$STAGE" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$TMPD/$idx.ll" 2>"$TMPD/$idx.err"
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

# Stage-1: emit IR → clang link → run for each test file. Full suite (core + compiler);
# this is the local/master gate — keep it running BOTH dirs (DoD #5, `just test`).
[group('test')]
test-stdlib-stage1: (_test-stdlib "build/compile_driver_bin_stage1")

# Stage-1, core only (tests/stdlib/*, excluding the tests/stdlib/compiler/ subdir).
# CI runs this on every PR; the compiler subdir is gated on compiler-affecting paths
# (see .github/workflows/ci.yml "Detect compiler-affecting changes"). The glob in
# _test-stdlib is non-recursive, so "tests/stdlib" does NOT pull in the compiler subdir.
[group('test')]
test-stdlib-core-stage1: (_test-stdlib "build/compile_driver_bin_stage1" "tests/stdlib")

# Stage-1, compiler subdir only (tests/stdlib/compiler/*). The 58 self-hosted-compiler
# suites each re-bundle the whole compiler (~260k IR lines, ~15s emit); ~61% of the
# stdlib-test CPU. PR-gated to compiler-affecting paths; always run on master + nightly.
[group('test')]
test-stdlib-compiler-stage1: (_test-stdlib "build/compile_driver_bin_stage1" "tests/stdlib/compiler")

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
  TMP_ERR="/tmp/sprout_build_$$.err"
  TMP_BIN="{{out_bin}}.$$.tmp"
  trap 'rm -f "$TMP_LL" "$TMP_ERR" "$TMP_BIN"' EXIT
  echo "==> Emitting LLVM IR via {{in_bin}}..."
  # A source error now sets a NONZERO exit and reports on stderr, so the exit
  # status is the primary signal. `{{in_bin}}` may still be an OLD stage built
  # before that change, which reported on stdout and exited 0 — so both streams
  # are checked and the status is captured rather than allowed to abort under
  # `set -e`. That keeps this recipe able to bootstrap from either generation of
  # compiler, which matters because it is the recipe the seed bootstrap runs
  # through. Without it, refreshing the seed across this change would need a
  # hand-built compiler.
  emit_status=0
  ./{{in_bin}} --emit-ir "{{stdlib_root}}" "{{driver}}" > "$TMP_LL" 2>"$TMP_ERR" || emit_status=$?
  cat "$TMP_ERR" >&2 || true
  # Diagnostics are "ERROR: bundle: ..." (parse) or "<line>:<col>: ERROR: check: ..."
  # (typecheck) — both anchored at line start. The prefix guards against matching
  # "ERROR:" inside emitted IR string constants (which begin with @.str).
  if [[ "$emit_status" -ne 0 ]] || grep -qE "^([0-9]+:[0-9]+: )?ERROR:" "$TMP_LL" "$TMP_ERR"; then
    echo "ERROR: compile failed while emitting IR via {{in_bin}} (exit $emit_status) — source error:" >&2
    grep -hE "^([0-9]+:[0-9]+: )?ERROR:" "$TMP_LL" "$TMP_ERR" 2>/dev/null | head -8 >&2
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
_compile-examples stage xfail="" srcs="examples/*.sprout examples/*/*.sprout" label="example":
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  XFAIL_EXAMPLES="{{xfail}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  JOBS=$(bash scripts/test_jobs.sh)
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
  for f in {{srcs}}; do
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
  [[ $total_xfail -gt 0 ]] && echo "==> $total_xfail {{label}}(s) xfail (expected)"
  if [ "$total_failed" -gt 0 ]; then
    echo "==> $total_failed {{label}}(s) FAILED"
    exit 1
  fi
  if [ "$idx" -eq 0 ]; then
    echo "==> ERROR: no {{label}} sources matched — the glob is stale, not the tree empty"
    exit 1
  fi
  echo "==> All ${idx} {{label}}s compiled OK"

# Stage-1: emit IR → clang link for each example. (This lane registers the repo as
# a package root, so examples importing examples.* — e.g. sentry_issue_browser —
# resolve and compile.)
[group('examples')]
compile-examples-stage1: (_compile-examples "build/compile_driver_bin_stage1" "")

# Same pipeline over bench/. Until 2026-08-11 NOTHING compiled bench/ — not this
# file, not `gate`, not CI — so it rotted silently while looking maintained. Two
# concrete costs, both found by hand rather than by a gate:
#
#   * `bench/http_worker_pool/{pool,spawn}_server.sprout` leaked a TcpConnection on
#     every read timeout (the `P0` linear facet), with a comment above it asserting
#     the obligation was discharged "on every path". It was discharged on one of three.
#   * `bench/unboxed_read` needed migrating for the `P0` fallible-bind rule and would
#     have broken the build the moment anyone ran it.
#
# Benches are compiled and linked, NOT run: they are deliberately long-running, so
# their value here is that they keep type-checking and lowering as the language moves.
[group('examples')]
compile-bench: (_compile-examples "build/compile_driver_bin_stage1" "" "bench/*.sprout bench/*/*.sprout" "bench file")

# Negative-diagnostic conformance: each tests/conformance/<dir>/<n>.spr must be
# rejected by `--phase check` with output containing the substring in <n>.err.
# Covers both parse-phase and type-phase rejections — `--phase check` runs the
# bundler (parse) first, so a parse error surfaces here too. (Matching is by
# output CONTENT, not exit status: these fixtures assert a specific DIAGNOSTIC, and
# a nonzero status alone cannot distinguish the expected diagnostic from a
# different rejection. `--phase check` does now exit nonzero on a source error —
# it previously exited 0 — so `|| true` below is what keeps `set -e` from aborting
# on the very rejection each fixture is asserting.) <noun> labels the summary; xfail =
# fixtures whose expected diagnostic is not yet produced (tracked TODO).
[private]
_test-reject stage dir noun xfail="":
  #!/usr/bin/env bash
  set -euo pipefail
  STAGE="{{stage}}"
  XFAIL="{{xfail}}"
  if [[ ! -x "./$STAGE" ]]; then
    echo "ERROR: $STAGE not found" >&2; exit 1
  fi
  # Fan out JOBS-wide. Each fixture is an independent `--phase check` process, so
  # this loop was pure serial latency: 99 type-error fixtures cost ~58s on ONE core
  # while _test-stdlib right next door ran JOBS-wide. Per-fixture output goes to its
  # own file and is replayed in fixture order at the end, so parallelism does not
  # interleave or reorder the report.
  JOBS=$(bash scripts/test_jobs.sh)
  TMPD=$(mktemp -d /tmp/sprout_reject_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  declare -a fixtures=()
  for spr in tests/conformance/{{dir}}/*.spr; do
    [ -f "$spr" ] || continue
    fixtures+=("$spr")
  done
  declare -a pids=()
  idx=0
  active=0
  for spr in "${fixtures[@]}"; do
    (
      set +e
      name="$(basename "${spr%.spr}")"
      err="tests/conformance/{{dir}}/$name.err"
      echo "==> $name" > "$TMPD/$idx.out"
      if [[ ! -f "$err" ]]; then
        echo "  MISSING .err" >> "$TMPD/$idx.out"; echo fail > "$TMPD/$idx.st"; exit 0
      fi
      is_xfail=0
      for xf in $XFAIL; do [[ "$name" == "$xf" ]] && is_xfail=1 && break; done
      expected="$(cat "$err")"
      out="$("./$STAGE" --phase check "{{stdlib_root}}" "$spr" 2>&1)"
      if echo "$out" | grep -qF -- "$expected"; then
        if [[ $is_xfail -eq 1 ]]; then
          echo "  UNEXPECTED MATCH (remove from xfail)" >> "$TMPD/$idx.out"; echo fail > "$TMPD/$idx.st"
        else
          echo "  OK (rejected)" >> "$TMPD/$idx.out"; echo ok > "$TMPD/$idx.st"
        fi
      else
        if [[ $is_xfail -eq 1 ]]; then
          echo "  xfail (expected diagnostic not yet produced)" >> "$TMPD/$idx.out"; echo xfail > "$TMPD/$idx.st"
        else
          echo "  FAILED: expected output to contain: $expected" >> "$TMPD/$idx.out"; echo fail > "$TMPD/$idx.st"
        fi
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
  for (( i = 0; i < idx; i++ )); do
    cat "$TMPD/$i.out" 2>/dev/null || true
    # A missing verdict means the subshell died before writing one — count it as a
    # failure rather than silently passing.
    case "$(cat "$TMPD/$i.st" 2>/dev/null || echo fail)" in
      ok) ;;
      xfail) total_xfail=$((total_xfail + 1)) ;;
      *) total_failed=$((total_failed + 1)) ;;
    esac
  done
  echo ""
  [[ $total_xfail -gt 0 ]] && echo "==> $total_xfail {{noun}} fixture(s) xfail (expected)"
  if [ "$total_failed" -gt 0 ]; then
    echo "==> $total_failed {{noun}} fixture(s) FAILED"
    exit 1
  fi
  echo "==> All {{noun}} fixtures rejected as expected"

# Stage-1 negative type-checking gate. No xfail — every fixture is expected to
# be rejected with its diagnostic. (Overlapping-instance and do-block
# family-conflict diagnostics landed in PR-3; missing_nested_instance{,_maybe}
# via the resolve pass in #110.)
[group('test')]
test-type-errors: (_test-reject "build/compile_driver_bin_stage1" "type_error" "type-error" "")

# Stage-1 negative parse gate: tests/conformance/parse_error/<n>.spr must be
# rejected at parse time with the diagnostic substring in <n>.err.
[group('test')]
test-parse-errors: (_test-reject "build/compile_driver_bin_stage1" "parse_error" "parse-error" "")

# Stage-1 executable-entrypoint gate: a DEFINED `main` with a malformed signature
# (nonzero args, non-Unit/Int return, pure, or effect-polymorphic) is rejected by
# validate_entrypoint at check time. `missing_main` is xfail — enforcing a REQUIRED
# main needs an explicit executable-vs-library compile mode (a library legitimately
# has none, e.g. examples/sentry_api.sprout); tracked in BACKLOG §7.3.
[group('test')]
test-executable-errors: (_test-reject "build/compile_driver_bin_stage1" "executable_error" "executable-error" "missing_main")

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

# Loud-fail guard.  A call to a callee that resolves to NOTHING must be
# DIAGNOSED, never silently zero-filled into `ret i64 0`.  This is the
# regression guard for the strictness that replaced direct codegen's
# `zero_val` fallback, which used to disguise bundler/iface gaps as
# GC/typeclass/print bugs.
#
# REWRITTEN 2026-08-07 — the original probe had rotted in three independent
# ways, and each one alone was enough to make the gate meaningless:
#
#   1. It grepped for "unresolved call".  That string was a `panic` in
#      `emit_named_call` at codegen.sprout:2601, and that FILE was deleted when
#      direct codegen was retired.  The string now exists nowhere in the tree
#      except, formerly, this recipe's own body.
#   2. Its probe was an importless `print(int_to_string(5))`, asserted to fail.
#      But `int_to_string` is a runtime builtin whose `declare i64
#      @int_to_string(i64)` ir_header emits unconditionally, and `print` is a
#      compiler intrinsic — so an importless call to either is resolvable BY
#      DESIGN.  That program compiles, links, and correctly prints 5.
#   3. It detected failure via exit status and read the message from stderr.
#      Before the ERROR-stream fix in this same PR, the driver reported source
#      errors on STDOUT and exited 0, so `if <compile>; then fail` could never
#      fire and `grep <stderr>` could never match — independent of 1 and 2.
#
# So the probe now uses a name that is neither a builtin nor an intrinsic, and
# reads BOTH streams.  Reading both is deliberate rather than lazy: it keeps the
# assertion about the DIAGNOSTIC, not about which stream carries it, so this gate
# stays honest whether or not a future change moves diagnostics between streams.
# The exit-status and stderr-specific assertions live in `diagnostic-stream-smoke`,
# which is where a stream regression belongs.
#
# The final check is a POSITIVE CONTROL.  Without it, "no IR was emitted" would
# pass vacuously if the compiler ever stopped emitting IR at all — a gate that
# cannot distinguish "correctly rejected" from "totally broken" is not a gate.
[group('smoke')]
loud-fail-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_lfs_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  DRIVER="{{build_dir}}/compile_driver_bin_stage1"
  # Neither a runtime builtin nor a compiler intrinsic, and deliberately
  # unmistakable in a diff so nobody "helpfully" defines it later.
  printf 'fn main() -> Unit !{IO} =\n  print(int_to_string(sprout_lfs_undefined_callee(5)))\n' > "$TMPD/unresolved.spr"
  "$DRIVER" --emit-ir "{{stdlib_root}}" "$TMPD/unresolved.spr" > "$TMPD/out.ll" 2>"$TMPD/err" || true

  if ! grep -qs "ERROR: check: Unknown variable" "$TMPD/out.ll" "$TMPD/err"; then
    echo "loud-fail-smoke: an undefined callee was NOT diagnosed." >&2
    echo "  expected 'ERROR: check: Unknown variable' on either stream; got:" >&2
    echo "  --- stdout ---" >&2; head -20 "$TMPD/out.ll" >&2
    echo "  --- stderr ---" >&2; head -20 "$TMPD/err" >&2
    exit 1
  fi
  # The zero-fill regression itself: a rejected program must yield NO code.
  if grep -qs "^define " "$TMPD/out.ll"; then
    echo "loud-fail-smoke: rejected program still emitted IR (silent zero-fill regression):" >&2
    grep -s "^define " "$TMPD/out.ll" | head -5 >&2
    exit 1
  fi

  # Positive control — the same shape WITHOUT the undefined callee must emit IR.
  printf 'fn main() -> Unit !{IO} =\n  print(int_to_string(5))\n' > "$TMPD/ok.spr"
  "$DRIVER" --emit-ir "{{stdlib_root}}" "$TMPD/ok.spr" > "$TMPD/ok.ll" 2>"$TMPD/ok.err" || true
  if ! grep -qs "^define " "$TMPD/ok.ll"; then
    echo "loud-fail-smoke: POSITIVE CONTROL failed — a resolvable program emitted no IR," >&2
    echo "  so the 'no IR' assertion above proves nothing.  Compiler or stdlib_root is broken:" >&2
    head -20 "$TMPD/ok.err" >&2; head -5 "$TMPD/ok.ll" >&2
    exit 1
  fi
  echo "==> loud-fail-smoke ✓ (undefined callee diagnosed, no IR emitted, control emits IR)"

# Diagnostic-stream guard.  A source error must go to STDERR and set a NONZERO
# exit status; stdout carries the artifact (IR / iface / status lines) and
# nothing else.
#
# Why this is a gate and not a style preference.  `--emit-ir` is used as
# `compile_driver --emit-ir <root> f.spr > f.ll` — the documented dev loop in
# AGENTS.md pipes it straight into clang.  When a diagnostic goes to stdout it
# physically BECOMES the .ll file, so a Sprout type error surfaces as a clang
# parse error quoting the Sprout error text:
#
#     error: expected top-level entity
#         1 | 1:1: ERROR: check: Executable entrypoint `main` must declare …
#
# compile_driver.sprout already states this rule for --emit-iface ("Errors go to
# stderr so they don't pollute the iface artifact when stdout is redirected to a
# file"); --emit-ir has the identical requirement and used to violate it.
#
# The exit status matters independently: with errors on stdout AND exit 0, no
# caller could detect failure at all, which is how `loud-fail-smoke` came to be
# structurally incapable of firing.  Note the whole negative-test suite is
# exit-status-blind by construction (`_test-reject` runs `2>&1 || true` and greps
# text), so nothing else in the tree covers this.
[group('smoke')]
diagnostic-stream-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -euo pipefail
  TMPD=$(mktemp -d /tmp/sprout_dss_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  DRIVER="{{build_dir}}/compile_driver_bin_stage1"
  FIX="tests/diagnostic_stream"
  failed=0
  for f in unknown_variable parse_error valid; do
    [[ -f "$FIX/$f.spr" ]] || { echo "diagnostic-stream-smoke: missing fixture $FIX/$f.spr" >&2; exit 1; }
  done

  # A plain check error (unknown variable) — the most common diagnostic.
  status=0
  "$DRIVER" --emit-ir "{{stdlib_root}}" "$FIX/unknown_variable.spr" > "$TMPD/bad.out" 2>"$TMPD/bad.err" || status=$?

  if ! grep -qs "ERROR: check: Unknown variable" "$TMPD/bad.err"; then
    echo "diagnostic-stream-smoke: diagnostic did NOT reach stderr." >&2
    echo "  --- stdout (should hold IR only) ---" >&2; head -5 "$TMPD/bad.out" >&2
    echo "  --- stderr (should hold the error) ---" >&2; head -5 "$TMPD/bad.err" >&2
    failed=1
  fi
  if grep -qsE "^([0-9]+:[0-9]+: )?ERROR:" "$TMPD/bad.out"; then
    echo "diagnostic-stream-smoke: diagnostic leaked into STDOUT — it would become the .ll file:" >&2
    grep -sE "^([0-9]+:[0-9]+: )?ERROR:" "$TMPD/bad.out" | head -3 >&2
    failed=1
  fi
  if [[ "$status" -eq 0 ]]; then
    echo "diagnostic-stream-smoke: a rejected program exited 0 — callers cannot detect failure." >&2
    failed=1
  fi

  # Parse errors take a different path (bundler, not checker) — cover it too.
  pstatus=0
  "$DRIVER" --emit-ir "{{stdlib_root}}" "$FIX/parse_error.spr" > "$TMPD/p.out" 2>"$TMPD/p.err" || pstatus=$?
  if ! grep -qs "ERROR: bundle:" "$TMPD/p.err"; then
    echo "diagnostic-stream-smoke: parse diagnostic did not reach stderr:" >&2
    head -5 "$TMPD/p.out" "$TMPD/p.err" >&2; failed=1
  fi
  if [[ "$pstatus" -eq 0 ]]; then
    echo "diagnostic-stream-smoke: a parse error exited 0." >&2; failed=1
  fi

  # Positive control — a VALID program must exit 0, put IR on stdout, and keep
  # stderr free of diagnostics.  Without this the checks above would also pass
  # on a compiler that rejected everything.
  okstatus=0
  "$DRIVER" --emit-ir "{{stdlib_root}}" "$FIX/valid.spr" > "$TMPD/ok.out" 2>"$TMPD/ok.err" || okstatus=$?
  if [[ "$okstatus" -ne 0 ]]; then
    echo "diagnostic-stream-smoke: POSITIVE CONTROL — a valid program exited $okstatus:" >&2
    head -10 "$TMPD/ok.err" >&2; failed=1
  fi
  if ! grep -qs "^define " "$TMPD/ok.out"; then
    echo "diagnostic-stream-smoke: POSITIVE CONTROL — a valid program emitted no IR on stdout." >&2
    failed=1
  fi
  if grep -qsE "^([0-9]+:[0-9]+: )?ERROR:" "$TMPD/ok.err"; then
    echo "diagnostic-stream-smoke: POSITIVE CONTROL — a valid program wrote a diagnostic to stderr:" >&2
    grep -sE "^([0-9]+:[0-9]+: )?ERROR:" "$TMPD/ok.err" | head -3 >&2; failed=1
  fi

  # --check-iface must never SUCCEED SILENTLY. Its own contract (compile_driver.sprout) promises a
  # caller can gate on the exit status "instead of scraping text", and an unreadable path defeated
  # that completely: `contents <- read_file(path)` discarded the Err and returned early, so the
  # driver printed NOTHING and exited 0 for a file it never read. Found 2026-08-11 by the
  # discarded-fallible-bind measurement (docs/fallible-bind-diagnostic-v0.md) — it was the only
  # production hit in the whole tree.
  #
  # Two cases, because they take different paths: readable-but-undecodable, and unreadable.
  for case in "$FIX/malformed.iface" "$TMPD/definitely-not-here.iface"; do
    istatus=0
    "$DRIVER" --check-iface "$case" > "$TMPD/i.out" 2>"$TMPD/i.err" || istatus=$?
    if [[ "$istatus" -eq 0 ]]; then
      echo "diagnostic-stream-smoke: --check-iface '$case' exited 0 — a status-gating caller sees SUCCESS." >&2
      failed=1
    fi
    if ! grep -qs "^INVALID: " "$TMPD/i.out"; then
      echo "diagnostic-stream-smoke: --check-iface '$case' printed no INVALID line:" >&2
      head -3 "$TMPD/i.out" "$TMPD/i.err" >&2; failed=1
    fi
    # The greppable contract `just check-iface-all` consumes is `^OK:`; it must be absent here.
    if grep -qs "^OK: " "$TMPD/i.out"; then
      echo "diagnostic-stream-smoke: --check-iface '$case' claimed OK:" >&2
      head -3 "$TMPD/i.out" >&2; failed=1
    fi
  done

  if (( failed > 0 )); then exit 1; fi
  echo "==> diagnostic-stream-smoke ✓ (errors on stderr, nonzero exit, stdout artifact-only, check-iface never silently OK)"

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

# Stdout-flush-on-abnormal-exit regression (CI gate). A program prints a sentinel
# line, THEN overflows the native stack (SIGSEGV -> crash handler -> `_exit`,
# which bypasses stdio flushing). With stdout captured to a FILE it is fully
# buffered by libc, so without the startup `setvbuf(stdout, _IOLBF)` the sentinel
# is discarded on `_exit` and lost. The gate asserts the pre-crash line survives.
# RED signal (fix missing/reverted): the sentinel is absent from captured stdout.
[group('smoke')]
flush-on-crash-smoke: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_flush_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  FIXTURE=tests/stack_overflow_smoke/print_then_overflow.spr
  RDYN=""; [ "$(uname)" != "Darwin" ] && RDYN="-rdynamic"
  if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" "$FIXTURE" > "$TMPD/out.ll" 2>"$TMPD/emit.err"; then
    echo "flush-on-crash-smoke: emit-IR failed" >&2; cat "$TMPD/emit.err" >&2; exit 1
  fi
  if ! clang "$TMPD/out.ll" {{runtime_src}} -O2 $RDYN {{clang_extra}} -o "$TMPD/bin" 2>"$TMPD/link.err"; then
    echo "flush-on-crash-smoke: link failed" >&2; cat "$TMPD/link.err" >&2; exit 1
  fi
  # stdout -> a regular file so libc fully buffers it (the case where the bug bites).
  set +e
  "$TMPD/bin" > "$TMPD/run.out" 2>"$TMPD/run.err"
  ec=$?
  set -e
  if [ "$ec" -eq 0 ]; then
    echo "flush-on-crash-smoke: fixture did NOT crash (exit 0) — recursion folded; make it deeper" >&2
    exit 1
  fi
  if ! grep -q "SENTINEL_BEFORE_CRASH" "$TMPD/run.out"; then
    echo "flush-on-crash-smoke: pre-crash stdout was LOST (buffered output discarded on _exit)" >&2
    echo "  -> startup setvbuf(stdout, _IOLBF) missing or reverted; see sprout_set_argv" >&2
    exit 1
  fi
  echo "==> flush-on-crash-smoke ✓ (pre-crash stdout survived a crash, exit $ec)"

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
  # (2e) tcp_read_some argument validation: an unallocated handle returns Err(TcpInvalidHandle) and a
  # non-positive max_bytes returns an Err too — never an abort. Deterministic (no socket timing).
  # Reaching "done" proves both Err branches; a regression to the fatal tcp_read behaviour exits
  # non-zero before printing it.
  build tests/task_io_smoke/tcp_read_some_bad_args.spr
  run_once "tcp-read-some-bad-args" "done"
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
  # (18) connect-park (L0.3 regression): `tcp_connect` must park on an in-flight connect rather than
  # blocking the OS thread, so a `with_timeout` deadline can fire and force-drop it — and that drop
  # must CLOSE the in-flight socket (it is not in the handle table, so nothing else ever will).
  # Run under a 64-descriptor cap: that is what gives the no-leak half of the fixture teeth, since
  # a leak then exhausts the table mid-loop and connect reports "Too many open files" (verified as a
  # negative control by disabling the close). Unfixed, the blocking connect burns ~7.5 s of frozen
  # scheduler per attempt on macOS and minutes on Linux — RED as a missing marker or as a hang.
  build tests/task_io_smoke/connect_park.spr
  ( ulimit -n 64; run_once "connect-park" "connect-park-ok" )
  ( ulimit -n 64; SPROUT_GC_STRESS=1 run_once "connect-park/stress" "connect-park-ok" )
  # (19) http_server idle-connection timeout (C3): a peer that connects and sends NOTHING must be
  # answered 408 and closed on a TOTAL header deadline, instead of parking its handler forever and
  # leaking the connection handle (slowloris). The same fixture's prompt client must still get 200,
  # so an over-eager timeout is RED too. Unfixed, the silent client's read never returns -> HANG.
  build tests/task_io_smoke/http_idle_timeout.spr
  run_once "http-idle-timeout" "silent-got-408"
  run_once "http-idle-timeout/served" "prompt-got-200"
  SPROUT_GC_STRESS=1 run_once "http-idle-timeout/stress" "silent-got-408"
  # (20) http_server header-flood bound: a peer that streams bytes with no "\r\n\r\n" keeps every
  # read succeeding, so NO deadline can fire — (19)'s timeout does not bound it and the header
  # accumulator grew for the whole budget. max_header_bytes must reject it while it is still
  # flooding, and the server must keep serving afterwards. Verified RED against the pre-cap
  # http_server ("server accepted an unbounded header block").
  build tests/task_io_smoke/http_header_flood.spr
  run_once "http-header-flood" "flood-bounded"
  SPROUT_GC_STRESS=1 run_once "http-header-flood/stress" "flood-bounded"
  # (21) http_server response-WRITE bound: bounding the read left the same handle exhaustion
  # reachable from the write side — a client that requests a large response and then stops reading
  # (without closing) parked its handler in send() forever. The fixture asserts on TRUNCATION, not
  # on termination: a client that merely slept and closed would let the unfixed server finish too
  # (its close resets the connection), so the discriminator is that the bounded server delivers only
  # what the kernel had buffered while the unfixed one delivers the whole 8 MiB body once the client
  # finally drains. Verified RED against the unbounded write ("delivered 8388697 bytes").
  build tests/task_io_smoke/http_write_timeout.spr
  run_once "http-write-timeout" "write-bounded"
  SPROUT_GC_STRESS=1 run_once "http-write-timeout/stress" "write-bounded"
  # (22) http_server BODY-phase timeout, and total-vs-idle. Nothing previously drove
  # continue_read_request -> read_remaining_body at all. Two clients: one completes its headers,
  # promises a body and sends none (must get 408 — the coverage half), one dribbles 5 bytes every
  # 200 ms for ~800 ms with a 300 ms body budget (must get 200 — the semantics half, since nginx's
  # client_body_timeout is idle-based, not total). Verified RED against a total body deadline
  # ("server hung up on a body that was still making progress").
  build tests/task_io_smoke/http_body_timeout.spr
  run_once "http-body-timeout" "stalled-body-408"
  run_once "http-body-timeout/idle" "dribbled-body-200"
  SPROUT_GC_STRESS=1 run_once "http-body-timeout/stress" "dribbled-body-200"
  # (23) The two bounds that make handler occupancy finite, and so unblock a bounded worker pool: an
  # over-large Content-Length refused 413 on the announcement, and a body crawling BELOW min_rate_bps
  # cut off at 408. The third marker is the control that matters more than either: a client above the
  # floor must still be served, because the failure mode of a rate floor is killing legitimate slow
  # uploads. RED as a 200 for the oversized body, a hang for the crawler (the old idle deadline was
  # re-armed forever by its steady trickle), or a 408 for the prompt client.
  build tests/task_io_smoke/http_body_bounds.spr
  run_once "http-body-bounds" "huge-body-413"
  run_once "http-body-bounds/rate" "below-rate-408"
  run_once "http-body-bounds/control" "above-rate-200"
  SPROUT_GC_STRESS=1 run_once "http-body-bounds/stress" "huge-body-413"
  # (24) serve_pooled: a FIXED worker pool instead of a task per connection. Two workers serve five
  # connections, so markers 3-5 can only appear if a worker looped back for more work — a pool whose
  # workers do not loop answers the first two and then hangs silently, which is the failure this
  # exists to make loud. Marker 3 drives a handler into the 500 fallback and markers 4-5 prove the
  # pool did not lose capacity to it. RED as a hang (no `done`) or as missing later markers.
  build tests/task_io_smoke/http_pooled_serve.spr
  run_once "http-pooled-serve" "pooled-5-200"
  run_once "http-pooled-serve/fallback" "pooled-3-500"
  SPROUT_GC_STRESS=1 run_once "http-pooled-serve/stress" "pooled-5-200"
  # (24) read_avail_timeout's two contract ends, both previously untested: `timeout_ms <= 0` polls
  # ONCE and reports a timeout without parking (what makes http_server's total header budget
  # composable — it passes the remaining slice), and a live budget still delivers data that arrives
  # inside it (which is what catches a fix that just always reports a timeout).
  build tests/task_io_smoke/read_timeout_poll_once.spr
  run_once "read-poll-once" "poll-once-timed-out"
  run_once "read-poll-once/data" "bounded-read-got-data"
  # (24b) A read must not report a timeout while its data is already buffered. Forces the fd-ready
  # event and the expired deadline into ONE poll batch (write at t=0, deadline at t=50ms, a
  # CPU-bound sibling delaying the poll to t=200ms), which is the case where pump_loop must pick
  # the fd over the timer. Both backends harvest in activation order today, so the data event is
  # first and this passes; it is pinned because that ordering is an undocumented property of the
  # epoll/kqueue ready list, not a contract, and this is the assertion that would catch it changing.
  build tests/task_io_smoke/read_deadline_loses_to_data.spr
  run_once "read-deadline-loses-to-data" "reader got ping"
  # (25) Content-Length is denominated in BYTES while the body path measured and cut in CODEPOINTS
  # (concurrency review C5). `café` is 5 bytes / 4 codepoints and separates the two; an ASCII body
  # cannot, which is why the fixtures above all missed it. Three paths, because they used different
  # length calls: the read loop (body in a second write), the already-buffered fast path (one write),
  # and a byte count cutting INSIDE a character. The last is a liveness check too — the obvious
  # implementation (`str_slice_bytes`) calls tcp_fail on a split codepoint, so it would let that one
  # request kill the server. RED as a 408 for the split client (a complete body looked one byte short,
  # so the loop waited for a byte already sent), a 500 for either (handler saw a mis-framed body), or
  # no output at all for the cut client (process aborted).
  build tests/task_io_smoke/http_utf8_body.spr
  run_once "http-utf8-body" "split-multibyte-200"
  run_once "http-utf8-body/inline" "inline-multibyte-200"
  run_once "http-utf8-body/cut" "split-codepoint-400"
  SPROUT_GC_STRESS=1 run_once "http-utf8-body/stress" "split-multibyte-200"
  # (26) Binary request bodies, now that the body is Bytes rather than String. The payload is
  # 0x00 0xFF 0x41 so each old failure mode is separately fatal: a strlen-based accumulator stops at
  # the NUL, a UTF-8 validator rejects the 0xFF, and the trailing 'A' is missing if anything truncated.
  # Both paths are covered because they used different code: headers+body in one write (already
  # buffered) and body in a second write (the read loop). RED as a 400 (validator still on the read
  # path), a 500 (handler saw a corrupted body), or a hang.
  build tests/task_io_smoke/http_binary_body.spr
  run_once "http-binary-body" "binary-inline-200"
  run_once "http-binary-body/split" "binary-split-200"
  SPROUT_GC_STRESS=1 run_once "http-binary-body/stress" "binary-inline-200"
  # (27) W2 R2 regression at the `net` layer: a payload containing 0x00 read back over loopback must
  # arrive as intact Bytes AND be refused by bytes.to_string. The deleted read builtins ended in
  # sprout_gc_adopt_cstr, minting a String that violates docs/spec-v0.md:64 — silently (byte_length 3,
  # length 1) with HDRCHECK off, and as an abort with it on. http-binary-body covers the same property
  # through the HTTP server; this pins it where the defect lived, so a regression is attributed to
  # `net` rather than diagnosed through a request parser. Run under HDRCHECK as well as the default,
  # since the default build is exactly what let this reach CI unnoticed.
  # (28) C3: tcp_accept is RECOVERABLE. Accepting on a listener handle that was never allocated must
  # return Err(TcpInvalidHandle), not abort. Deterministic — no socket timing — and this is the load-
  # bearing coverage of the contract; the exhaustion fixture below is the end-to-end half.
  build tests/task_io_smoke/tcp_accept_bad_handle.spr
  run_once "tcp-accept-bad-handle" "done"
  # (29) C3, end-to-end: a server under descriptor pressure must SURVIVE and still serve afterwards.
  # `ulimit -n` has to be imposed before the process starts, hence the subshell. Read the fixture's
  # header before treating this as EMFILE coverage: the branch is only reached in a narrow band of
  # limits (measured: hit at 32-40 on macOS, missed at 24 and at 64), so a run that does not reach it
  # still passes. What is asserted unconditionally is survival — on the unfixed runtime any accept
  # failure aborted the process, so the marker was unreachable.
  build tests/task_io_smoke/http_accept_exhaustion.spr
  ( ulimit -n 32; run_once "http-accept-exhaustion" "served-after-descriptor-pressure" )
  build tests/task_io_smoke/tcp_nul_payload.spr
  run_once "tcp-nul-payload" "nul-bytes-intact"
  run_once "tcp-nul-payload/decode" "nul-decode-refused"
  SPROUT_GC_HDRCHECK=1 run_once "tcp-nul-payload/hdrcheck" "nul-bytes-intact"
  echo "==> task-io-smoke ✓ (read-park, accept-park, re-arm, http-serve-concurrency, http-conn-error-isolation, tcp-read-some-bad-args, write, cancel-drop, await-guard, timer-drop, timeout-drop, timeout-nested-guard, chan-cancel-drop, chan-timeout-drop, chan-negative-cap-guard, rendezvous-send-drop, send-on-closed-guard, double-close-guard, send-parked-close-guard, select-cancel-drop, select-timeout-drop, connect-park, http-idle-timeout, http-header-flood, http-write-timeout, http-body-timeout, http-body-bounds, http-pooled-serve, read-poll-once, read-deadline-loses-to-data, http-utf8-body, http-binary-body, tcp-accept-bad-handle, http-accept-exhaustion, tcp-nul-payload; interleaved; stress-clean)"

# ── Linux gate (local, container-backed) ──────────────────────────────────────
#
# WHY THIS EXISTS. Sprout's I/O layer has two poll backends and a developer Mac can
# only run one of them: kqueue on macOS, epoll + timerfd on Linux. Every other local
# gate therefore certifies the backend CI does NOT use. That divergence is not
# cosmetic — both of these landed as red CI runs on branches that were green on every
# local gate:
#
#   * `task_sleep` arms a TIMERFD on Linux — a file DESCRIPTOR — but an EVFILT_TIMER on
#     the already-open kqueue on macOS, needing no descriptor. So a descriptor-
#     exhaustion back-off written with `task_sleep` required the very resource it was
#     recovering from, on Linux only. See tests/task_io_smoke/http_accept_exhaustion.spr.
#   * accept(2) on Linux passes ALREADY-PENDING network errors (ENETDOWN, EPROTO,
#     EHOSTUNREACH, …) through to the caller; BSD does not. A macOS run cannot reach
#     the branch that handles them at all.
#
# `just linux-smoke` closes the hole: it runs `task-io-smoke` — the gate that covers the
# whole park/timer/socket surface — inside a Linux container against the working tree,
# under CI's SPROUT_GC_HDRCHECK=1.
#
# PROVEN RED SIGNAL. This is not a decorative gate. At commit 4dcfad79, whose CI run
# failed, it reproduces that failure verbatim:
#     task-io-smoke [http-accept-exhaustion]: did not complete (exit 1) …
#     runtime error: builtin `task_sleep`: could not arm a timer (descriptor exhaustion?)
# and it is green at 681a9fe8, the commit that fixed it. Cost: ~14s to link stage-1 from
# the seed plus ~1m45s for the 34 fixtures, against a ~13min CI round-trip.
#
# WHAT IT DOES NOT COVER. The container is the host's architecture, while CI is x86_64.
# This catches OPERATING-SYSTEM asymmetry — the epoll/timerfd backend, Linux errno
# semantics, glibc — which is what has actually bitten us twice. It does NOT catch ISA
# asymmetry. Tracked in BACKLOG. Forcing --platform linux/amd64 is deliberately NOT done
# here: it would silently route every compile through QEMU emulation, and a gate slow
# enough to skip is a gate that does not run.
#
# NOT wired into `gate` or `ci-fast-gates`, deliberately: CI already runs on Linux, so a
# container there is pure waste, and a container runtime is not a required contributor
# dependency. This is opt-in, before pushing changes to runtime/, the scheduler, or the
# net/http_server stack.

# Build the pinned Linux toolchain image if it is absent (one-off, ~1 GB, ~1min).
[group('smoke')]
linux-image:
  #!/usr/bin/env bash
  set -euo pipefail
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: linux-image needs a running Linux container runtime; \`docker info\` failed." >&2
    echo "  colima users: colima start" >&2
    exit 1
  fi
  if docker image inspect "{{linux_image}}" >/dev/null 2>&1; then exit 0; fi
  LOG=$(mktemp /tmp/sprout_linux_image_XXXXXX.log)
  trap 'rm -f "$LOG"' EXIT
  echo "==> Building {{linux_image}} (one-off, ~880MB)..."
  # Dockerfile on stdin => EMPTY build context, so the repo is never uploaded to the
  # daemon across colima's sshfs mount.
  if ! docker build -t "{{linux_image}}" - < scripts/linux_smoke.Dockerfile 2>&1 | tee "$LOG"; then
    if grep -q "No space left on device" "$LOG"; then
      echo "" >&2
      echo "ERROR: the container VM's disk is full — the image is ~880MB plus transient apt space." >&2
      echo "  Inspect first (some 'reclaimable' volumes are real databases):" >&2
      echo "      docker system df -v" >&2
      echo "  Non-destructive fix — colima grows its disk in place on restart (>= v0.5.3):" >&2
      echo "      colima stop && colima start --disk <bigger-GiB>" >&2
    fi
    exit 1
  fi
  echo "==> Built {{linux_image}}."

# Fetch the pinned `just` as a static musl binary matching the container's architecture,
# verified against the official release checksum. The host's `just` is a macOS binary and
# cannot run in the container, and the container image deliberately has no just.
_linux-just: linux-image
  #!/usr/bin/env bash
  set -euo pipefail
  DEST="{{linux_cache}}/just-{{linux_just_version}}"
  if [[ -x "$DEST" ]]; then exit 0; fi
  # Ask the IMAGE for its architecture rather than querying the daemon with a Go
  # --format template: uname is the value that actually decides which binary can
  # execute, and a Go template's doubled braces are read as just interpolation —
  # inside a shebang recipe even a COMMENT containing them is a parse error.
  ARCH="$(docker run --rm "{{linux_image}}" uname -m)"
  case "$ARCH" in
    aarch64) SHA="{{linux_just_sha_aarch64}}"; TRIPLE="aarch64-unknown-linux-musl" ;;
    x86_64)  SHA="{{linux_just_sha_x86_64}}";  TRIPLE="x86_64-unknown-linux-musl" ;;
    *) echo "ERROR: no pinned \`just\` checksum for container arch '$ARCH'." >&2; exit 1 ;;
  esac
  TMPD=$(mktemp -d /tmp/sprout_linuxjust_XXXXXX)
  trap 'rm -rf "$TMPD"' EXIT
  URL="https://github.com/casey/just/releases/download/{{linux_just_version}}/just-{{linux_just_version}}-${TRIPLE}.tar.gz"
  echo "==> Fetching just {{linux_just_version}} ($TRIPLE)..."
  curl -sSfL -o "$TMPD/just.tgz" "$URL"
  GOT="$(shasum -a 256 "$TMPD/just.tgz" | cut -d' ' -f1)"
  if [[ "$GOT" != "$SHA" ]]; then
    echo "ERROR: checksum mismatch for $URL" >&2
    echo "  expected $SHA" >&2
    echo "  got      $GOT" >&2
    exit 1
  fi
  tar xzf "$TMPD/just.tgz" -C "$TMPD" just
  mkdir -p "{{linux_cache}}"
  mv "$TMPD/just" "$DEST"
  chmod +x "$DEST"
  echo "==> Cached $DEST"

# Run an arbitrary just recipe inside the Linux container: `just linux-run test-stdlib-core-stage1`.
[group('smoke')]
linux-run *ARGS: _linux-just
  #!/usr/bin/env bash
  set -euo pipefail
  REPO="{{justfile_directory()}}"
  # The container sees the repo through the VM's $HOME mount. A repo outside $HOME
  # silently appears as an EMPTY directory rather than failing, which presents as a
  # baffling "file not found" from the compiler — so refuse up front.
  if [[ "$REPO" != "$HOME"/* ]]; then
    echo "ERROR: linux-run needs the repo under \$HOME ($HOME); it is at $REPO." >&2
    echo "  The container runtime only exposes \$HOME to the VM, so any other path mounts empty." >&2
    exit 1
  fi
  # Three of the flags below are load-bearing, not tuning:
  #
  #  :ro on the repo — the container physically cannot mutate the working tree, so no
  #    root-owned files can appear in it. Recipes that must write are out of scope.
  #
  #  --set build_dir /tmp/build — MANDATORY. build/compile_driver_bin_stage1 is a NATIVE
  #    binary and bootstrap-from-seed's no-op guard is a bare mtime test
  #    (`[[ "$OUT" -nt "$SEED" ]]`). A container writing into the shared build/ would
  #    leave a Linux ELF that the host then considers UP TO DATE: every host gate fails
  #    to exec it while the guard insists no rebuild is needed. CI avoids the same trap
  #    by keying its stage-1 cache on runner.os as well as the file hashes.
  #
  #  --tmpfs /tmp:rw,exec — docker's --tmpfs defaults to NOEXEC, which breaks both just's
  #    shebang recipes and every fixture binary the gate compiles and runs.
  docker run --rm \
    -v "$REPO":/work:ro \
    -v "{{linux_cache}}":/opt/sprout-just:ro \
    --tmpfs /tmp:rw,exec,size=3g \
    -w /work \
    -e SPROUT_GC_HDRCHECK=1 \
    "{{linux_image}}" \
    "/opt/sprout-just/just-{{linux_just_version}}" --set build_dir /tmp/build {{ARGS}}

# HTTP CLIENT binary-body regression (code review finding 8): a response body containing 0x00 or
# non-UTF-8 bytes must arrive byte-for-byte on BOTH body paths (Content-Length and chunked). Before
# the fix the runtime re-measured the body with strlen, so `AAAA\0BBBB...` arrived as 4 bytes and an
# `Ok`, and the chunked path failed outright with "truncated chunk data".
#
# Its own recipe rather than a tests/task_io_smoke fixture because it needs a peer PROCESS: the
# `http_request` builtin is blocking, so a Sprout server task and a Sprout client in one process
# deadlock. Uses python3, which scripts/seed_gate.sh already assumes.
[group('test')]
http-client-binary-gate: bootstrap-from-seed
  SPROUT_STAGE1="{{build_dir}}/compile_driver_bin_stage1" bash scripts/http_client_binary_gate.sh

# The curated Linux gate: the park/timer/socket surface, on the backend CI uses.
[group('smoke')]
linux-smoke: (linux-run "task-io-smoke")
  @echo "==> linux-smoke ✓ (task-io-smoke on Linux, epoll+timerfd backend, HDRCHECK on)"

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
  # SPROUT_FL_VERIFY is set alongside the stress flag below: it checks the sweep's
  # staged per-class freelists against a full-heap walk on EVERY collection, and
  # collect-on-every-allocation is the setting that maximises the number of those
  # checks. It costs O(heap) + two sorts per collection, so it is NOT enabled job-
  # wide (that triples a compile-heavy run) and is skipped per-file below where the
  # combination is disproportionate.
  # test_gc_freelist_reuse: drives the freelist cases that produce a wrong list
  # rather than a crash — slots free across a cycle boundary, and regions whose
  # staged entries must be kept or dropped depending on whether Pass 2 releases them.
  STRESS_FILES="tests/stdlib/test_gc_freelist_reuse.spr tests/stdlib/test_ir_rooting.spr tests/stdlib/test_ir_codegen_ctors.spr tests/stdlib/test_ir_codegen_match.spr tests/stdlib/test_ir_codegen_closures.spr tests/stdlib/test_ir_codegen_char_rooting.spr tests/stdlib/test_stress_global_roots.spr tests/stdlib/test_stress_unboxed_maybe_heap_payload.spr tests/stdlib/test_stress_cpr_tier2_worker.spr tests/stdlib/test_stress_records_heap.spr tests/stdlib/test_task_cooperative.spr tests/stdlib/test_task_nested_scope.spr tests/stdlib/test_chan.spr tests/stdlib/test_chan_close.spr tests/stdlib/test_chan_rendezvous.spr tests/stdlib/test_chan_select.spr"
  # Known-failing under stress — false-green at the default threshold, FOUND BY
  # THIS PASS (residual typed-codegen rooting UAF, GC-confirmed via
  # SPROUT_GC_DISABLE).  Tracked in BACKLOG.md; warn-only here.  Promote to
  # STRESS_FILES as each is fixed (an UNEXPECTED PASS flags that it's ready).
  STRESS_XFAIL=""
  # Files that run under stress but WITHOUT the freelist oracle, because the two
  # together are disproportionate.  test_ir_codegen_ctors is the serial critical
  # path of this whole recipe: measured locally (5 jobs) it runs 180s under stress
  # alone and 434s with the oracle, while all 15 other files finish in under 2s
  # each either way — so the oracle on that one file is +254s (CI: 300s -> 883s for
  # the step) for heap shapes `just test-freelist-verify` already walks at the
  # default threshold.  Keep this list minimal and justify each entry with a
  # measurement; the oracle is ON by default so new stress files are covered.
  FL_VERIFY_SKIP=" test_ir_codegen_ctors "
  failed=0
  JOBS=$(bash scripts/test_jobs.sh)
  run_one() {  # prints "ok" or "fail"; never exits.  Per-file err file avoids the
               # shared-$TMPD/err race when invoked concurrently.
    local f="$1" name ll bin out err fv=1
    name=$(basename "$f" .spr); ll="$TMPD/$name.ll"; bin="$TMPD/$name.bin"; err="$TMPD/$name.err"
    case "$FL_VERIFY_SKIP" in *" $name "*) fv=0 ;; esac
    "{{build_dir}}/compile_driver_bin_stage1" --use-ir-codegen "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$ll" 2>"$err" || { echo fail; return; }
    clang "$ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$bin" 2>"$err" || { echo fail; return; }
    if out=$(SPROUT_GC_STRESS=1 SPROUT_FL_VERIFY=$fv "$bin" 2>&1); then
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

# Freelist oracle at the DEFAULT threshold (SPROUT_FL_VERIFY=1, no GC stress).
#
# `test-stress` already runs the oracle, but only under SPROUT_GC_STRESS=1, which
# forces small heaps — and a small heap barely makes Pass 2 release any region,
# which is the case the sweep's freelist staging exists to handle. These files
# carry multi-region heaps that die wholesale, so they need the default threshold
# to stay affordable. Both halves are needed: measured with a release counter,
# test_gc_region_release drives 14 region releases against the reuse test's 1.
#
# Mutation-tested: dropping the earlier-cycle re-list is caught at cycle 7, and
# omitting fl_region_rollback is caught at cycle 6 as a named dangling entry.
[group('test')]
test-freelist-verify: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_flverify_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "test-freelist-verify: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
  FILES="tests/stdlib/test_gc_region_release.spr tests/stdlib/test_gc_freelist_reuse.spr"
  failed=0
  for f in $FILES; do
    [ -f "$f" ] || { echo "test-freelist-verify: missing $f" >&2; failed=$((failed + 1)); continue; }
    name=$(basename "$f" .spr)
    if ! "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$TMPD/$name.ll" 2>"$TMPD/$name.err"; then
      echo "test-freelist-verify: compile failed: $f" >&2; cat "$TMPD/$name.err" >&2; failed=$((failed + 1)); continue
    fi
    if ! clang "$TMPD/$name.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/$name.bin" 2>"$TMPD/$name.err"; then
      echo "test-freelist-verify: link failed: $f" >&2; cat "$TMPD/$name.err" >&2; failed=$((failed + 1)); continue
    fi
    if out=$(SPROUT_FL_VERIFY=1 SPROUT_STDLIB_ROOT="{{stdlib_root}}" "$TMPD/$name.bin" 2>&1); then
      if echo "$out" | grep -q "SUITE FAILED"; then
        echo "test-freelist-verify: $f FAILED" >&2; echo "$out" >&2; failed=$((failed + 1))
      else
        echo "  PASS (fl-verify): $f"
      fi
    else
      echo "test-freelist-verify: $f aborted under SPROUT_FL_VERIFY=1" >&2
      echo "$out" | grep FL_VERIFY >&2 || echo "$out" | tail -5 >&2
      failed=$((failed + 1))
    fi
  done
  if (( failed > 0 )); then
    echo "test-freelist-verify: $failed file(s) failed" >&2; exit 1
  fi
  echo "==> test-freelist-verify ✓"

# Calibration gate for the object-age instrument (SPROUT_GC_AGEPROF=1), which
# measures how much of each collection's mark work is spent on objects that
# already survived a previous cycle — i.e. the ceiling on what a generational
# nursery could skip.  A counter is only evidence if it can come out DIFFERENT on
# inputs whose answer is known, so this runs two workloads that differ only in
# whether a large structure is held live across collections and asserts the gap:
#   retain_all  -> marked_age_ge1 ratio HIGH  (retained chain re-marked every cycle)
#   retain_none -> marked_age_ge1 ratio LOW   (nothing survives a cycle)
# A stuck, inverted, or live-vs-marked-confused counter fails here.  The runtime
# separately aborts on internal inconsistency (histogram vs totals), so this gate
# only has to check the separation.
[group('test')]
gc-ageprof-check: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_ageprof_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "gc-ageprof-check: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
  # Emits "<name> <marked_ratio> <died_young_ratio>" or exits non-zero.
  run_one() {
    local name="$1" f="tests/stdlib/$1.spr"
    [ -f "$f" ] || { echo "gc-ageprof-check: missing $f" >&2; return 1; }
    "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$TMPD/$name.ll" 2>"$TMPD/$name.err" \
      || { echo "gc-ageprof-check: compile failed: $f" >&2; cat "$TMPD/$name.err" >&2; return 1; }
    clang "$TMPD/$name.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/$name.bin" 2>"$TMPD/$name.err" \
      || { echo "gc-ageprof-check: link failed: $f" >&2; cat "$TMPD/$name.err" >&2; return 1; }
    # SPROUT_GC_ADAPT_FACTOR is PINNED, not left at the default, because the measured
    # re-mark ratio is a function of collection frequency: fewer collections mean each
    # object's first (age-0) mark is a larger share of a smaller total, so raising the
    # factor lowers the ratio without anything being wrong.  (Raising the default 2.0
    # -> 3.0 moved retain_all from 73% to 52% and broke this gate's 70% bound.)  This
    # gate validates the age COUNTER; `gc-adapt-check` covers the threshold POLICY.
    # Keeping them separate means a future factor change cannot silently recalibrate
    # the instrument's own correctness check.
    SPROUT_GC_AGEPROF=1 SPROUT_GC_ADAPT_FACTOR=2 "$TMPD/$name.bin" > "$TMPD/$name.out" 2>"$TMPD/$name.prof" \
      || { echo "gc-ageprof-check: $f aborted under SPROUT_GC_AGEPROF=1" >&2; tail -5 "$TMPD/$name.prof" >&2; return 1; }
    grep -q "SUITE PASSED" "$TMPD/$name.out" \
      || { echo "gc-ageprof-check: $f workload did not pass" >&2; tail -5 "$TMPD/$name.out" >&2; return 1; }
    local line
    line=$(grep -m1 "ageprof.*marked_total=" "$TMPD/$name.prof") \
      || { echo "gc-ageprof-check: $f emitted no ageprof summary (is SPROUT_GC_AGEPROF wired up?)" >&2; return 1; }
    awk -v n="$name" '{
      for (i = 1; i <= NF; i++) { split($i, kv, "="); v[kv[1]] = kv[2] + 0 }
      if (v["marked_total"] <= 0) { print "gc-ageprof-check: " n ": marked_total=0, workload drove no marking" > "/dev/stderr"; exit 1 }
      if (v["freed_total"]  <= 0) { print "gc-ageprof-check: " n ": freed_total=0, workload freed nothing"    > "/dev/stderr"; exit 1 }
      printf "%s %d %d\n", n, (100 * v["marked_age_ge1"]) / v["marked_total"], (100 * v["freed_age0"]) / v["freed_total"]
    }' <<< "$line"
  }
  ALL=$(run_one test_gc_age_retain_all)   || exit 1
  NONE=$(run_one test_gc_age_retain_none) || exit 1
  all_marked=$(awk '{print $2}' <<< "$ALL");  all_young=$(awk '{print $3}' <<< "$ALL")
  none_marked=$(awk '{print $2}' <<< "$NONE"); none_young=$(awk '{print $3}' <<< "$NONE")
  echo "  retain_all : marked_age_ge1=${all_marked}%  died_young=${all_young}%"
  echo "  retain_none: marked_age_ge1=${none_marked}%  died_young=${none_young}%"
  failed=0
  # Thresholds are deliberately loose — this gate checks that the instrument
  # DISCRIMINATES, not that a workload hits a precise number.  The separation
  # bound is the load-bearing one; a stuck counter passes the two one-sided
  # bounds only if it happens to sit inside both, which it cannot.
  (( all_marked >= 70 ))                  || { echo "gc-ageprof-check: retain_all marked_age_ge1 ${all_marked}% < 70% — retained chain is not dominating mark work" >&2; failed=1; }
  (( none_marked <= 15 ))                 || { echo "gc-ageprof-check: retain_none marked_age_ge1 ${none_marked}% > 15% — objects are surviving cycles that should not" >&2; failed=1; }
  (( all_marked - none_marked >= 40 ))    || { echo "gc-ageprof-check: separation $(( all_marked - none_marked ))pp < 40pp — the counter does not discriminate" >&2; failed=1; }
  (( none_young >= 90 ))                  || { echo "gc-ageprof-check: retain_none died_young ${none_young}% < 90% — weak generational hypothesis not reproduced on pure churn" >&2; failed=1; }
  (( failed == 0 )) || exit 1
  echo "==> gc-ageprof-check ✓"

# Pin the adaptive-threshold policy (`threshold = max(live x adapt_factor, floor)`).
# Two properties, both load-bearing for the `adapt_factor` default:
#   1. The DEFAULT collects as sparsely as an explicit F=3, and strictly less often
#      than the old F=2 default.  This is what makes the default a policy and not an
#      accident — an accidental revert to 2.0 fails here.
#   2. On a workload whose live set is below the threshold floor, the factor is
#      PROVABLY INERT: cycles/marked/freed are bit-identical at F=2 and F=4.  This is
#      the safety argument for raising the default at all (small programs cannot pay
#      RSS for it), so it is asserted rather than assumed.
# Reuses the age-instrument workloads: retain_all holds a 150k-node chain live (live
# set >> floor, so the factor binds), retain_none retains nothing (floor-pinned).
# Cycle counts come from SPROUT_GC_AGEPROF and are deterministic run to run, so the
# assertions can be exact rather than ranged.
[group('test')]
gc-adapt-check: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_adapt_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "gc-adapt-check: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
  build_one() {
    local name="$1" f="tests/stdlib/$1.spr"
    [ -f "$f" ] || { echo "gc-adapt-check: missing $f" >&2; return 1; }
    "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "$f" > "$TMPD/$name.ll" 2>"$TMPD/$name.err" \
      || { echo "gc-adapt-check: compile failed: $f" >&2; cat "$TMPD/$name.err" >&2; return 1; }
    clang "$TMPD/$name.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/$name.bin" 2>"$TMPD/$name.err" \
      || { echo "gc-adapt-check: link failed: $f" >&2; cat "$TMPD/$name.err" >&2; return 1; }
  }
  # Emits "<cycles> <marked_total> <freed_total>" for one binary at one factor.
  # An empty SPROUT_GC_ADAPT_FACTOR means "use the compiled-in default".
  probe() {
    local name="$1" factor="$2" line
    if [ -n "$factor" ]; then export SPROUT_GC_ADAPT_FACTOR="$factor"; else unset SPROUT_GC_ADAPT_FACTOR; fi
    line=$(SPROUT_GC_AGEPROF=1 "$TMPD/$name.bin" 2>"$TMPD/$name.prof" >"$TMPD/$name.out"; grep -m1 "ageprof.*cycles=" "$TMPD/$name.prof") \
      || { echo "gc-adapt-check: $name emitted no ageprof summary at factor='${factor:-default}'" >&2; return 1; }
    grep -q "SUITE PASSED" "$TMPD/$name.out" \
      || { echo "gc-adapt-check: $name did not pass at factor='${factor:-default}'" >&2; tail -5 "$TMPD/$name.out" >&2; return 1; }
    awk '{ for (i = 1; i <= NF; i++) { split($i, kv, "="); v[kv[1]] = kv[2] + 0 }
           printf "%d %d %d\n", v["cycles"], v["marked_total"], v["freed_total"] }' <<< "$line"
  }
  build_one test_gc_age_retain_all  || exit 1
  build_one test_gc_age_retain_none || exit 1
  failed=0
  # --- Property 1: the default is the loose factor, not the old 2.0 -------------
  read -r def_cyc def_marked _   < <(probe test_gc_age_retain_all "")  || exit 1
  read -r f2_cyc  f2_marked  _   < <(probe test_gc_age_retain_all 2)   || exit 1
  read -r f3_cyc  f3_marked  _   < <(probe test_gc_age_retain_all 3)   || exit 1
  echo "  retain_all: default cycles=${def_cyc} marked=${def_marked} | F=2 cycles=${f2_cyc} marked=${f2_marked} | F=3 cycles=${f3_cyc} marked=${f3_marked}"
  (( def_cyc == f3_cyc && def_marked == f3_marked )) \
    || { echo "gc-adapt-check: default (cycles=${def_cyc}) does not match F=3 (cycles=${f3_cyc}) — SPROUT_GC_ADAPT_FACTOR default is not 3.0" >&2; failed=1; }
  (( def_cyc < f2_cyc )) \
    || { echo "gc-adapt-check: default collects as often as F=2 (${def_cyc} vs ${f2_cyc}) — the adaptive factor is not taking effect" >&2; failed=1; }
  # Marking is the whole point of the knob: fewer passes over the same live set.
  (( f3_marked * 100 < f2_marked * 70 )) \
    || { echo "gc-adapt-check: F=3 mark work ${f3_marked} is not <70% of F=2's ${f2_marked} — no mark-work reduction" >&2; failed=1; }
  # --- Property 2: below the floor, the factor is inert -------------------------
  none_f2=$(probe test_gc_age_retain_none 2) || exit 1
  none_f4=$(probe test_gc_age_retain_none 4) || exit 1
  echo "  retain_none (floor-pinned): F=2 '${none_f2}' | F=4 '${none_f4}'  (cycles marked freed)"
  [ "$none_f2" = "$none_f4" ] \
    || { echo "gc-adapt-check: floor-pinned workload changed with the factor ('${none_f2}' vs '${none_f4}') — raising the default is NOT free for small-live-set programs" >&2; failed=1; }
  (( failed == 0 )) || exit 1
  echo "==> gc-adapt-check ✓"

# Prove the O(1) arena lookup path is actually TAKEN, and that its fallback works.
# This gate exists because the optimisation is invisible to every other test: if the
# reservation fails or is mis-sized, `region_find` silently reverts to the binary
# search and the whole change becomes a no-op that still passes the full suite.
# (Same failure shape as the PR #48 freelist oracle — an instrument that never
# reaches its state looks identical to one that works.)
#
# Asserts both directions:
#   1. Default: normal regions live in the arena (arena_regions > 0) and NOTHING
#      overflowed (overflow_regions == 0), so lookups take the shift path.
#   2. SPROUT_GC_ARENA_MB=0: the arena is disabled, everything overflows, and the
#      workload still passes — the graceful-degradation path is executed, not assumed.
[group('test')]
gc-arena-check: bootstrap-from-seed
  #!/usr/bin/env bash
  set -uo pipefail
  TMPD=$(mktemp -d /tmp/sprout_arena_XXXXXX); trap 'rm -rf "$TMPD"' EXIT
  mkdir -p "$TMPD/rtobj"
  for rtsrc in {{runtime_src}}; do
    clang -c "$rtsrc" -O2 {{clang_extra}} -o "$TMPD/rtobj/$(basename "$rtsrc" .c).o" 2>"$TMPD/rt.err" \
      || { echo "gc-arena-check: runtime compile failed ($rtsrc)" >&2; cat "$TMPD/rt.err" >&2; exit 1; }
  done
  NAME=test_gc_age_retain_all   # allocates a 150k-node chain: many normal regions
  "{{build_dir}}/compile_driver_bin_stage1" --emit-ir "{{stdlib_root}}" --package-root "{{justfile_directory()}}" "tests/stdlib/$NAME.spr" > "$TMPD/w.ll" 2>"$TMPD/w.err" \
    || { echo "gc-arena-check: compile failed" >&2; cat "$TMPD/w.err" >&2; exit 1; }
  clang "$TMPD/w.ll" "$TMPD/rtobj"/*.o {{clang_extra}} -o "$TMPD/w.bin" 2>"$TMPD/w.err" \
    || { echo "gc-arena-check: link failed" >&2; cat "$TMPD/w.err" >&2; exit 1; }
  # Emits "<max arena_regions> <max overflow_regions>" over ALL logged cycles.
  # Maxima, not the last cycle: the final collection can run after the workload's
  # data is already dead, so the last line understates occupancy — and a maximum
  # of 0 overflow is a stronger claim than 0 at one arbitrary instant.
  probe() {
    local label="$1"; shift
    env "$@" SPROUT_DEBUG_GC=1 "$TMPD/w.bin" > "$TMPD/$label.out" 2>"$TMPD/$label.err" \
      || { echo "gc-arena-check: workload failed under $label" >&2; tail -5 "$TMPD/$label.err" >&2; return 1; }
    grep -q "SUITE PASSED" "$TMPD/$label.out" \
      || { echo "gc-arena-check: workload did not pass under $label" >&2; tail -5 "$TMPD/$label.out" >&2; return 1; }
    grep -q "arena_regions=" "$TMPD/$label.err" \
      || { echo "gc-arena-check: no arena_regions field in SPROUT_DEBUG_GC output under $label — is the arena instrumented?" >&2; return 1; }
    awk '/arena_regions=/ {
           for (i = 1; i <= NF; i++) { split($i, kv, "="); v[kv[1]] = kv[2] + 0 }
           if (v["arena_regions"]    > a) a = v["arena_regions"]
           if (v["overflow_regions"] > o) o = v["overflow_regions"]
         } END { printf "%d %d\n", a, o }' "$TMPD/$label.err"
  }
  failed=0
  read -r on_arena on_overflow < <(probe default) || exit 1
  echo "  default            : max arena_regions=${on_arena} max overflow_regions=${on_overflow}"
  # > 1, not > 0: one region could be an artefact of the always-open bump region,
  # whereas several proves the arena is genuinely serving the allocation path.
  (( on_arena > 1 )) \
    || { echo "gc-arena-check: max arena_regions=${on_arena} with the arena enabled — the O(1) path is NOT being taken, so this optimisation is inert" >&2; failed=1; }
  (( on_overflow == 0 )) \
    || { echo "gc-arena-check: overflow_regions=${on_overflow} on a workload that should fit the reservation — lookups for those regions still binary-search" >&2; failed=1; }
  read -r off_arena off_overflow < <(probe disabled SPROUT_GC_ARENA_MB=0) || exit 1
  echo "  SPROUT_GC_ARENA_MB=0: max arena_regions=${off_arena} max overflow_regions=${off_overflow}"
  (( off_arena == 0 )) \
    || { echo "gc-arena-check: arena_regions=${off_arena} with the arena disabled — SPROUT_GC_ARENA_MB=0 is not honoured" >&2; failed=1; }
  (( off_overflow > 0 )) \
    || { echo "gc-arena-check: overflow_regions=0 with the arena disabled — the fallback path was never exercised" >&2; failed=1; }
  # 3. Deliberately undersized arena: BOTH paths live at once.  This is the riskiest
  #    configuration — every lookup picks between the shift path and the binary
  #    search on the same heap — and no other gate reaches it.
  read -r mix_arena mix_overflow < <(probe mixed SPROUT_GC_ARENA_MB=2) || exit 1
  echo "  SPROUT_GC_ARENA_MB=2: max arena_regions=${mix_arena} max overflow_regions=${mix_overflow}"
  (( mix_arena > 0 && mix_overflow > 0 )) \
    || { echo "gc-arena-check: undersized arena did not produce a mixed state (arena=${mix_arena} overflow=${mix_overflow}) — the both-paths-live configuration is untested" >&2; failed=1; }
  (( failed == 0 )) || exit 1
  echo "==> gc-arena-check ✓"

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
  JOBS=$(bash scripts/test_jobs.sh)
  # "<label>|<gate-command>"; labels are filesystem-safe (result/output filenames).
  GATES=(
    "approved-builtins|check-approved-builtins"
    "smoke-shapes|smoke-shapes"
    "bundle-smoke|bundle-smoke"
    "fmt-check|fmt-check"
    "type-errors|test-type-errors"
    "parse-errors|test-parse-errors"
    "executable-errors|test-executable-errors"
    "conformance-run|test-conformance-run"
    "example-canary|run-example-canary"
    "gc-safety|gc-safety-check --strict"
    "freelist-verify|test-freelist-verify"
    "gc-ageprof|gc-ageprof-check"
    "gc-adapt|gc-adapt-check"
    "gc-arena|gc-arena-check"
    "argv-smoke|argv-smoke"
    "div-by-zero-smoke|div-by-zero-smoke"
    "stack-overflow-smoke|stack-overflow-smoke"
    "flush-on-crash-smoke|flush-on-crash-smoke"
    "task-io-smoke|task-io-smoke"
    "http-client-binary|http-client-binary-gate"
    "tco-runtime-smoke|tco-runtime-smoke"
    "trace-dispatch-smoke|trace-dispatch-smoke"
    "verify-dispatch-smoke|verify-dispatch-smoke"
    "loud-fail-smoke|loud-fail-smoke"
    "diagnostic-stream-smoke|diagnostic-stream-smoke"
    "ir-golden-diff|ir-golden-diff"
    "gate-audit|gate-audit"
    # Added when Assertion D landed: both had names that CLAIM verification while nothing
    # ran them. c-runtime-test's ten C-level assertions were unrunnable for however long it
    # took someone to try (the runtime split into sprout_scheduler.c/sprout_poll.c broke its
    # link line and nothing noticed); b1-gate sat RED on master behind a fixture that
    # predated the explicit-`_` partial-application syntax.
    "c-runtime-test|c-runtime-test"
    "b1-gate|b1-gate"
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
#   just gate         full CI parity          (mirrors .github/workflows/ci.yml)
#   just gate-audit   guard: fails if CI runs a task `gate` misses, or if a
#                     scripts/ gate is invoked by nothing at all
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
gate: fmt-check smoke-shapes bundle-smoke loud-fail-smoke diagnostic-stream-smoke argv-smoke trace-dispatch-smoke verify-dispatch-smoke div-by-zero-smoke stack-overflow-smoke flush-on-crash-smoke tco-runtime-smoke c-runtime-test b1-gate check-approved-builtins verify-bootstrap-fixed-point ir-golden-diff compile-examples-stage1 compile-bench run-example-canary test task-io-smoke http-client-binary-gate test-stress
  #!/usr/bin/env bash
  set -euo pipefail
  echo "==> gate: gc-safety-check --strict..."
  just gc-safety-check --strict
  echo "==> gate ✓ — full CI-parity battery passed; CI will not surprise you."

# Drift guard, two independent assertions:
#
#   A. Every `just` task CI runs is covered by `gate`.  Computes gate's coverage
#      LIVE by recursively expanding its dependencies via `just --show` (so `test`
#      gaining a child needs no edit here), then diffs against the tasks grepped
#      out of the CI workflow.
#
#   B. Every gate script under scripts/ is REACHABLE — referenced by the justfile,
#      by a .claude hook, or explicitly allowlisted with a reason.  Assertion A
#      only checks the CI->gate direction, so it is blind to a script that no
#      recipe invokes at all: that rots silently while still LOOKING like
#      coverage, which is exactly how two stale tests/golden/ir/ snapshots reached
#      master while scripts/ir_golden_diff.sh was wired to nothing.  A corpus
#      nothing checks is worse than no corpus, because it reads as verified.
#
#   C. The CONVERSE of A: every task `gate` runs is also exercised in CI.  A and B
#      together still left a hole, and `loud-fail-smoke` fell straight through it:
#      it was listed in `gate` but absent from ci-fast-gates and the workflow, so
#      CI never ran it — and it sat RED on master for weeks with nothing to signal
#      that.  A cannot catch this (it only walks CI->gate) and B cannot either (it
#      guards orphaned scripts/*.sh, and this is a justfile recipe).  Note the
#      failure is worse than an un-run gate: `gate` aborts at the first failure, so
#      a gate-only recipe going red silently truncates the whole LOCAL battery
#      after it — here, everything past loud-fail-smoke, which is most of it.
#      Comparison is by dependency CLOSURE, not by name, because CI runs umbrella
#      recipes (`test`, `ci-fast-gates`) whose children it never names.
#
#   D. Every recipe whose NAME claims verification is reachable from `gate` or CI.
#      A, B and C all start from something that already runs, so none of them can
#      see a recipe nothing runs at all — the hole `c-runtime-test` and `b1-gate`
#      fell through. B even made it worse by validating a single hop:
#      scripts/b1_gate.sh passed as "reachable" because the `b1-gate` recipe named
#      it, while nothing ran that recipe.  D starts from the recipe LIST instead.
#
# Run after touching ci.yml, the gate list, or scripts/.
# Assert every CI task is gated and every gate script is reachable (drift guard).
[group('gate')]
gate-audit:
  #!/usr/bin/env bash
  set -euo pipefail
  CI_WORKFLOW=".github/workflows/ci.yml"
  # CI tasks gate covers under a DIFFERENT recipe name (the audit name-matches, so
  # it can't see equivalent coverage) or intentionally omits:
  #   bootstrap-from-seed / build-fmt-from-seed — auto-run build deps.
  #   refresh-seed — mutate-then-check seed path; gate covers it via verify-bootstrap-fixed-point.
  #   ci-fast-gates — the aggregate CI invokes; gate runs each constituent by name instead
  #     (smoke-shapes, bundle-smoke, fmt-check, check-approved-builtins, ir-golden-diff,
  #     the *-smoke regression gates), plus gc-safety-check (from gate's body) and test
  #     (which covers the type/parse/executable/conformance error suites ci-fast-gates
  #     also runs).  gate-audit itself is a ci-fast-gates member and needs no gate entry:
  #     it is a meta-guard over the gate list, so `gate` depending on it would be circular.
  #   test-stdlib-core-stage1 / test-stdlib-compiler-stage1 — the split suite CI runs; gate
  #     covers both via test → test-stdlib-stage1 (the combined core+compiler suite).
  EXCLUDE="bootstrap-from-seed build-fmt-from-seed refresh-seed ci-fast-gates test-stdlib-core-stage1 test-stdlib-compiler-stage1"
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

  # ── Assertion B: every scripts/ gate is reachable ───────────────────────────
  # Scripts that are deliberately NOT invoked from the justfile, each with the
  # reason it is unreachable BY DESIGN.  Adding a name here is a decision, not a
  # formality: it asserts "nothing should run this automatically".
  #   seed_gate.sh          — PreToolUse Bash hook (.claude/settings.json); intercepts
  #                           `git commit`, so a justfile recipe would be the wrong home.
  #   guidelines_reminder.sh — .claude hook; agent-facing prompt, not a build step.
  #   memwatch.sh           — interactive RSS observer for a running process; attaches
  #                           to a PID, so it has no non-interactive gate form.
  #   ir_byte_identical_check.sh — SITUATIONAL: asserts stage1 and stage2 emit
  #                           byte-identical IR, which holds only for a refactor
  #                           claimed behavior-preserving.  It fails BY DESIGN on any
  #                           intentional codegen change, so gating it would invert
  #                           its meaning.  Run it by hand to substantiate such a claim.
  SCRIPTS_EXCLUDE="seed_gate.sh guidelines_reminder.sh memwatch.sh ir_byte_identical_check.sh"
  unreachable=""
  shopt -s nullglob
  for s in scripts/*.sh; do
    b="$(basename "$s")"
    grep -qw "$b" <<<"$SCRIPTS_EXCLUDE" && continue
    # Reachable if the justfile invokes it, or a .claude hook wires it.
    grep -q "$b" justfile && continue
    grep -rq "$b" .claude/ 2>/dev/null && continue
    unreachable="$unreachable $b"
  done
  shopt -u nullglob
  if [[ -n "$unreachable" ]]; then
    echo "gate-audit ✗ — these scripts/ gates are invoked by NOTHING (they rot while looking like coverage):" >&2
    printf '   %s\n' $unreachable >&2
    echo "   Wire each into a just recipe (and into 'gate'/'ci-fast-gates' if it is a gate)," >&2
    echo "   delete it, or add it to SCRIPTS_EXCLUDE with the reason it must stay manual." >&2
    exit 1
  fi

  # ── Assertion C: every gate task is exercised in CI (the converse of A) ──────
  # Built from the dependency CLOSURE of everything CI invokes — both the tasks
  # named in ci.yml and the members of ci-fast-gates' own GATES array — so an
  # umbrella recipe covers its children without naming them.
  #
  # Recipes that are legitimately LOCAL-ONLY, each with the reason:
  #   gate — the battery itself.  CI deliberately runs the constituents in
  #          parallel via ci-fast-gates rather than `gate` sequentially, so `gate`
  #          appearing in its own closure is self-reference, not a gap.
  GATE_ONLY_EXCLUDE="gate"
  gates_arr=$(sed -n '/^  GATES=(/,/^  )/p' justfile \
                | grep -oE '"[^"]+"' | tr -d '"' | cut -d'|' -f2 | awk '{print $1}' | sort -u)
  if [[ -z "$gates_arr" ]]; then
    echo "gate-audit ✗ — could not parse ci-fast-gates' GATES array; assertion C would pass vacuously." >&2
    exit 1
  fi
  ci_closure=$(for t in $ci_tasks $gates_arr; do expand "$t"; done | sort -u)
  ungated=""
  for t in $gate_set; do
    grep -qw "$t" <<<"$GATE_ONLY_EXCLUDE" && continue
    grep -qx "$t" <<<"$ci_closure" || ungated="$ungated $t"
  done
  if [[ -n "$ungated" ]]; then
    echo "gate-audit ✗ — 'just gate' runs these tasks that CI never exercises:" >&2
    printf '   %s\n' $ungated >&2
    echo "   A gate CI never runs can go red unnoticed AND truncates the local battery" >&2
    echo "   after it (gate stops at the first failure). Add each to ci-fast-gates' GATES" >&2
    echo "   array or to the workflow, or to GATE_ONLY_EXCLUDE with the reason." >&2
    exit 1
  fi

  # ── Assertion D: every recipe that CLAIMS to verify is reachable ─────────────
  # A, B and C all start from something that already runs: A and C walk gate<->CI, B walks
  # scripts/. None of them can see a recipe that nothing runs at all. That is the hole
  # `c-runtime-test` and `b1-gate` fell through — and B made it worse by validating only ONE
  # hop: scripts/b1_gate.sh counted as "reachable" because the b1-gate recipe named it, while
  # nothing ran that recipe. A *_gate.sh was rotting behind the very assertion whose comment
  # says scripts "rot while looking like coverage".
  #
  # So D starts from the recipe LIST instead: any recipe whose name claims verification must
  # be reachable from `gate` or CI, or be excluded with a stated reason. Name-based is a
  # heuristic and deliberately so — a recipe called `foo-check` that nothing runs is a lie
  # whatever its body does, and the classifier costs nothing to widen.
  #
  # Measured when this landed (2026-08-11): 42 of 75 recipes were reachable from neither gate
  # nor CI. Most are legitimately manual (repl, run, build-*, llvm-where, gc-profile) and do
  # not claim verification. Of the seven that did, all were executed by hand: four were green,
  # check-iface-all needed a precondition, and the two wired in above were broken — one
  # totally (c-runtime-test could not link), one red (b1-gate's stale fixture).
  VERIFY_RE='(^|-)(test|tests|check|verify|gate|audit|smoke|lint)($|-)|-(diff|verify|check|gate|test|smoke|audit)$'
  # Exclusions are matched as WHOLE LINES (grep -qxF), not with -w: a hyphen is a non-word
  # character, so `grep -w test` would also match `test-file` and silently excuse it.
  # (printf rather than a heredoc: a heredoc's terminator must sit at column 0, and a
  # column-0 line ends a just recipe body.)
  VERIFY_EXCLUDE=$(printf '%s\n' gate gate-quick check lint test-file fmt-check-file \
                                 lint-file check-iface-all test-stdlib-stage2 linux-smoke)
  #   gate, gate-quick — the batteries themselves; C already covers gate's membership, and
  #     gate-quick is the deliberately-partial local subset (its point is being faster than CI).
  #   check, lint, test-file, fmt-check-file, lint-file — single-FILE / interactive developer
  #     entry points taking a path argument. There is no whole-repo form to gate, and the
  #     repo-wide equivalents (fmt-check, gc-safety-check, test) are all gated.
  #   check-iface-all — requires `just refresh-iface` first, so gating it as-is would fail on a
  #     missing precondition rather than a real defect. The .iface speedup wiring it belongs to
  #     is unbuilt (BACKLOG, iface-precompiled-modules); gate it together with that work.
  #   test-stdlib-stage2 — stage-2 suite. CI builds and tests stage-1 from the committed seed;
  #     stage-2 is the bootstrap's next hop, exercised by verify-bootstrap-fixed-point instead.
  #   linux-smoke — needs a container runtime, and CI already RUNS on Linux, so gating it there
  #     is pure waste. Its whole purpose is covering a platform CI has and developers do not.
  all_recipes=$(just --summary 2>/dev/null | tr ' ' '\n' | sort -u)
  if [[ -z "$all_recipes" ]]; then
    echo "gate-audit ✗ — could not enumerate recipes; assertion D would pass vacuously." >&2
    exit 1
  fi
  reachable=$(printf '%s\n%s\n' "$gate_set" "$ci_closure" | sort -u)
  orphan_gates=""
  for t in $all_recipes; do
    grep -qE "$VERIFY_RE" <<<"$t" || continue
    grep -qxF "$t" <<<"$VERIFY_EXCLUDE" && continue
    grep -qxF "$t" <<<"$reachable" || orphan_gates="$orphan_gates $t"
  done
  if [[ -n "$orphan_gates" ]]; then
    echo "gate-audit ✗ — these recipes CLAIM to verify something but nothing runs them:" >&2
    printf '   %s\n' $orphan_gates >&2
    echo "   A gate nobody runs is worse than no gate, because it reads as coverage." >&2
    echo "   Wire each into ci-fast-gates' GATES array (and 'gate'), or add it to" >&2
    echo "   VERIFY_EXCLUDE with the reason it must stay manual." >&2
    exit 1
  fi
  echo "==> gate-audit ✓ — gate covers every CI task; CI exercises every gate task; every scripts/ gate is reachable; every verification recipe is run."

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
