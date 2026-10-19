#!/usr/bin/env bash
# CI only (Ubuntu): install LLVM from apt.llvm.org and put its bindir on GITHUB_PATH. x86_64
# needs clang >= 22 (scripts/check_clang_cpr.sh). The build recipes call bare `clang` and
# `opt`, which only the versioned bindir carries.
#
# usage: ci_install_llvm.sh [--sanitizers] [extra apt packages…]
#   --sanitizers  also installs the ASan/UBSan runtime, which `clang` does not pull in
set -euo pipefail

LLVM_VERSION=22
pkgs=("llvm-$LLVM_VERSION" "clang-$LLVM_VERSION")
if [[ "${1:-}" == --sanitizers ]]; then
  pkgs+=("libclang-rt-$LLVM_VERSION-dev")
  shift
fi

# shellcheck disable=SC1091
. /etc/os-release
wget -qO- https://apt.llvm.org/llvm-snapshot.gpg.key \
  | sudo tee /etc/apt/trusted.gpg.d/apt.llvm.org.asc >/dev/null
echo "deb https://apt.llvm.org/$VERSION_CODENAME/ llvm-toolchain-$VERSION_CODENAME-$LLVM_VERSION main" \
  | sudo tee "/etc/apt/sources.list.d/llvm-$LLVM_VERSION.list"
sudo apt-get update
sudo apt-get install -y "${pkgs[@]}" "$@"

bindir="/usr/lib/llvm-$LLVM_VERSION/bin"
echo "$bindir" >> "$GITHUB_PATH"
"$bindir/clang" --version | head -1
"$bindir/opt" --version | sed -n 2p
