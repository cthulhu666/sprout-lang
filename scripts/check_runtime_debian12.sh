#!/usr/bin/env bash
# Compile runtime/*.c on Debian 12, whose glibc 2.36 gates more names behind feature-test macros
# than CI's Ubuntu 24.04 does (docs/gates.md §Debian 12).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker run --rm -v "$ROOT/runtime":/work/runtime:ro -w /work debian:12 bash -euc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends clang-16 libc6-dev >/dev/null
  ldd --version | head -n1
  clang-16 --version | head -n1
  clang-16 -fsyntax-only runtime/*.c
'
echo "==> runtime-debian12 ✓"
