#!/usr/bin/env bash
# Echoes the number of test jobs to run in parallel. Single source of truth for
# every parallel test recipe in the justfile (_test-stdlib, _test-reject,
# test-conformance-run), so the fan-out policy lives in one place.
#
# On a heterogeneous CPU (Apple Silicon: N Performance + M Efficiency cores),
# `hw.ncpu` counts BOTH, and spawning that many compiler processes makes the
# machine unusable while buying very little: the workload is CPU-bound on the
# Performance cores, so the Efficiency cores mostly add contention and heat.
# Measured on a 5P+6E machine, the full stdlib suite realized only ~5.5x
# parallel speedup at 8 jobs. So budget against the Performance cores.
#
# `hw.perflevel0` is Apple's fastest level (`hw.perflevel0.name` == "Performance");
# the sysctl is absent on Intel Macs and on Linux, which fall back to the total.
#
# Override with SPROUT_TEST_JOBS to dial fan-out up or down, e.g.
#   SPROUT_TEST_JOBS=2 just test    # leave the machine responsive
#   SPROUT_TEST_JOBS=16 just test   # a dedicated box with cores to burn
set -euo pipefail

if [[ -n "${SPROUT_TEST_JOBS:-}" ]]; then
  if [[ "$SPROUT_TEST_JOBS" =~ ^[1-9][0-9]*$ ]]; then
    echo "$SPROUT_TEST_JOBS"
    exit 0
  fi
  echo "ERROR: SPROUT_TEST_JOBS must be a positive integer (got '$SPROUT_TEST_JOBS')" >&2
  exit 1
fi

jobs=""
# macOS, heterogeneous cores: Performance cores only.
if perf=$(sysctl -n hw.perflevel0.logicalcpu 2>/dev/null); then
  [[ "$perf" =~ ^[1-9][0-9]*$ ]] && jobs="$perf"
fi
# Linux, or a homogeneous Mac: everything.
if [[ -z "$jobs" ]]; then
  total=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
  [[ "$total" =~ ^[1-9][0-9]*$ ]] && jobs="$total"
fi
[[ -n "$jobs" ]] || jobs=4

# Cap: past this, the suite is bound by memory bandwidth and clang link I/O
# rather than cores, and the extra processes only add contention.
if (( jobs > 8 )); then
  jobs=8
fi

echo "$jobs"
