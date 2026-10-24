#!/usr/bin/env bash
# Debian/Ubuntu: install LLVM from apt.llvm.org. In GitHub Actions, also put its bindir on
# GITHUB_PATH. x86_64 needs clang >= 22 (scripts/check_clang_cpr.sh). Build recipes discover
# the versioned bindir locally; CI needs GITHUB_PATH because later steps use bare `clang` and
# `opt` directly.
#
# usage: ci_install_llvm.sh [--sanitizers] [extra apt packages...]
#   --sanitizers  also installs the ASan/UBSan runtime, which `clang` does not pull in
set -euo pipefail

LLVM_VERSION=22
pkgs=("llvm-$LLVM_VERSION" "clang-$LLVM_VERSION")
if [[ "${1:-}" == --sanitizers ]]; then
  pkgs+=("libclang-rt-$LLVM_VERSION-dev")
  shift
fi

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "sudo is required to install LLVM packages." >&2
    exit 1
  fi
}

# shellcheck disable=SC1091
. /etc/os-release
curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key \
  | run_as_root tee /etc/apt/trusted.gpg.d/apt.llvm.org.asc >/dev/null
echo "deb https://apt.llvm.org/$VERSION_CODENAME/ llvm-toolchain-$VERSION_CODENAME-$LLVM_VERSION main" \
  | run_as_root tee "/etc/apt/sources.list.d/llvm-$LLVM_VERSION.list" >/dev/null
run_as_root apt-get update
run_as_root apt-get install -y "${pkgs[@]}" "$@"

bindir="/usr/lib/llvm-$LLVM_VERSION/bin"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$bindir" >> "$GITHUB_PATH"
fi
"$bindir/clang" --version | head -1
"$bindir/opt" --version | sed -n 2p
